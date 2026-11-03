/*
 * codec_harness.c -- decode verification & benchmark harness for the U2
 * A2DP sink codec path (SBC / AAC-LATM / LDAC).
 *
 * Purpose:
 *   1. Phase A verification: the SAME bitstream decoded by the -O0 build and
 *      the -O2(+NEON) build must produce BYTE-IDENTICAL PCM (no -ffast-math,
 *      -ffp-contract=off; FDK is fixed-point so trivially exact).
 *   2. Phase B verification: an optimized libldacdec (float/NEON) must match
 *      the vendored double-precision decoder to >= 90 dB PSNR.
 *   3. Timing: ns per decoded frame, old vs new objects (relative speedup).
 *
 * The decode paths replicate the production glue in btif_media_task.c:
 *   SBC : OI_CODEC_SBC_DecoderReset(ctx, data, sizeof, 2, 2, FALSE) +
 *         OI_CODEC_SBC_DecodeFrame loop
 *   AAC : aacDecoder_Open(TT_MP4_LATM_MCP1, 1) + aacDecoder_Fill +
 *         aacDecoder_DecodeFrame(flags=0) loop
 *   LDAC: ldacdecInit + ldacDecode per transport frame (RTP media-payload
 *         header is NOT present in harness files - the encoder emits pure
 *         ldac_transport_frame sequences, like btif's payload+1 view)
 *
 * Build: see build_harness.sh (static armv7 binary, runs under qemu-arm or
 * on-device).  Test bitstreams are generated with the vendored encoders
 * (embdrv SBC encoder, FDK libAACenc, Sony libldacBT_enc from AOSP 8.1
 * external/libldac) from a deterministic synthetic PCM signal.
 */

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ---- SBC (embdrv: BlueZ-style encoder + OI decoder, same as btif) ---- */
#include "oi_codec_sbc.h"
#include "sbc_encoder.h"

/* ---- AAC (Fraunhofer FDK, same as btif) ---- */
#include "aacdecoder_lib.h"
#include "aacenc_lib.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#include "tp_data.h"

/* ---- LDAC decode (vendored libldacdec, same as btif) ---- */
/* ldacdec.h uses 'this' as a parameter name (legal in C, keyword in C++):
   rename it around the include. */
#define this self_ldac
extern "C" {
#include "ldacdec.h"
}
#undef this

/* ---- LDAC encode (Sony libldacBT_enc, test-stream generation only) ---- */
#include "ldacBT.h"

#define GEN_SR        44100
#define GEN_CH        2
#define PCM_CHUNK     (1 << 16)          /* output write chunk (bytes)     */

/* ------------------------------------------------------------------ util */

static void die(const char *fmt, ...)
{
    va_list ap;
    __builtin_va_start(ap, fmt);
    fprintf(stderr, "harness: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    exit(2);
}

static uint8_t *read_file(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) die("cannot open %s: %s", path, strerror(errno));
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n <= 0) die("empty file %s", path);
    uint8_t *buf = (uint8_t *)malloc((size_t)n + 64);
    if (!buf) die("oom");
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) die("short read %s", path);
    fclose(f);
    buf[n] = 0;
    *len = (size_t)n;
    return buf;
}

static FILE *open_out(const char *path)
{
    FILE *f = fopen(path, "wb");
    if (!f) die("cannot create %s: %s", path, strerror(errno));
    return f;
}

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* ------------------------------------------------------- gen (test signal) */
/* Deterministic synthetic stereo s16le: sweep / noise / impulses / silence /
 * music-ish mix -- exercises tonal, noisy, transient and quiet passages. */

static uint32_t lcg_state;
static uint32_t lcg(void)
{
    lcg_state = lcg_state * 1664525u + 1013904223u;
    return lcg_state;
}

static int16_t clamp16(double v)
{
    if (v > 32767.0) return 32767;
    if (v < -32768.0) return -32768;
    return (int16_t)(v < 0 ? v - 0.5 : v + 0.5);
}

static int cmd_gen(int argc, char **argv)
{
    const char *out = argv[0];
    double secs = (argc > 1) ? atof(argv[1]) : 6.0;
    int sr = (argc > 2) ? atoi(argv[2]) : GEN_SR;
    long total = (long)(secs * sr);
    int16_t *pcm = (int16_t *)malloc((size_t)total * GEN_CH * sizeof(int16_t));
    if (!pcm) die("oom");

    lcg_state = 12345u;
    double ph0 = 0.0, ph1 = 0.0, ph2 = 0.0;
    long seg = total / 5;
    for (long i = 0; i < total; i++)
    {
        double t = (double)i / sr;
        double l, r;
        long s = (seg > 0) ? (i / seg) : 0;
        switch (s > 4 ? 4 : (int)s)
        {
        case 0: /* log sweep 50 Hz -> 15 kHz */
        {
            double f = 50.0 * pow(15000.0 / 50.0, (double)(i % (seg ? seg : 1)) / (double)(seg ? seg : 1));
            ph0 += 2.0 * M_PI * f / sr;
            l = 26000.0 * sin(ph0);
            r = 26000.0 * sin(ph0 + 0.3);
            break;
        }
        case 1: /* white-ish noise (LCG) */
            l = (double)(int16_t)(lcg() >> 16) * 0.9;
            r = (double)(int16_t)(lcg() >> 16) * 0.9;
            break;
        case 2: /* impulse train + 1 kHz tone (transients) */
            ph1 += 2.0 * M_PI * 1000.0 / sr;
            l = 20000.0 * sin(ph1);
            r = l;
            if ((i % 512) == 0) { l = 30000.0; r = -30000.0; }
            break;
        case 3: /* near-silence (dither / low-level path) */
            l = 60.0 * sin(2.0 * M_PI * 220.0 * t) + (double)(int16_t)(lcg() >> 28);
            r = 60.0 * sin(2.0 * M_PI * 221.0 * t) + (double)(int16_t)(lcg() >> 28);
            break;
        default: /* music-ish: 3 partials + light noise, stereo decorrelated */
            ph0 += 2.0 * M_PI * 440.0 / sr;
            ph1 += 2.0 * M_PI * 880.0 / sr;
            ph2 += 2.0 * M_PI * 554.4 / sr;
            l = 14000.0 * sin(ph0) + 7000.0 * sin(ph1) + 5000.0 * sin(ph2)
                + (double)(int16_t)(lcg() >> 19) * 0.05;
            r = 14000.0 * sin(ph0 + 0.5) + 7000.0 * sin(ph1) + 5000.0 * sin(ph2 + 1.1)
                + (double)(int16_t)(lcg() >> 19) * 0.05;
            break;
        }
        pcm[i * GEN_CH]     = clamp16(l);
        pcm[i * GEN_CH + 1] = clamp16(r);
    }

    FILE *f = open_out(out);
    fwrite(pcm, sizeof(int16_t), (size_t)total * GEN_CH, f);
    fclose(f);
    printf("gen: %s %ld frames (%.1fs) %d Hz %d ch s16le\n", out, total, secs, sr, GEN_CH);
    free(pcm);
    return 0;
}

/* ------------------------------------------------------------------- SBC */

static int cmd_enc_sbc(int argc, char **argv)
{
    size_t plen;
    uint8_t *pcm = read_file(argv[0], &plen);
    long nsamples = (long)(plen / (GEN_CH * sizeof(int16_t)));   /* per ch */
    long fpc = 16 * 8;                                            /* frames per block-set: 16 blocks * 8 subbands = 128 samples/ch/frame */
    fpc = 128;
    long nframes = nsamples / fpc;
    if (nframes <= 0) die("input too short");

    SBC_ENC_PARAMS enc;
    memset(&enc, 0, sizeof(enc));
    /* SBC_Encoder_Init expects *code* values, not raw Hz: SBC_sf44100==2 in
       this tree's sbc_encoder.h. Bitpool is DERIVED from u16BitRate inside
       Init (same formula as btif_media_task); 328 kbps -> bitpool 53 for
       44.1k joint-stereo 8 subbands 16 blocks (the A2DP high-quality point). */
    const char *cm = getenv("SBC_CHMODE");
    enc.s16SamplingFreq    = SBC_sf44100;
    enc.s16ChannelMode     = (cm && !strcmp(cm, "stereo")) ? SBC_STEREO : SBC_JOINT_STEREO;
    enc.s16NumOfSubBands   = 8;
    enc.s16NumOfChannels   = GEN_CH;
    enc.s16NumOfBlocks     = 16;
    enc.s16AllocationMethod = SBC_LOUDNESS;
    enc.u16BitRate         = 328;   /* kbps; s16BitPool computed by Init */
    enc.u8NumPacketToEncode = 1;
    SBC_Encoder_Init(&enc);

    uint8_t framebuf[2048];
    enc.pu8Packet = framebuf;
    FILE *f = open_out(argv[1]);
    for (long i = 0; i < nframes; i++)
    {
        /* SBC_NO_PCM_CPY_OPTION==FALSE in this tree: SBC_Encoder() overwrites
           ps16NextPcmBuffer with the internal as16PcmBuffer on entry, so the
           caller must memcpy PCM in first (exactly like btif_media_task.c
           L3511+L3564 does). */
        memcpy(enc.as16PcmBuffer, pcm + (size_t)i * fpc * GEN_CH * sizeof(int16_t),
               (size_t)fpc * GEN_CH * sizeof(int16_t));
        SBC_Encoder(&enc);
        if (enc.u16PacketLength == 0 || enc.u16PacketLength > sizeof(framebuf))
            die("sbc enc frame %ld: bad len %u", i, enc.u16PacketLength);
        fwrite(framebuf, 1, enc.u16PacketLength, f);
    }
    fclose(f);
    printf("enc-sbc: %ld frames -> %s\n", nframes, argv[1]);
    free(pcm);
    return 0;
}

/* shared SBC decode loop (used by dec-sbc and bench) */
static long sbc_decode_all(const uint8_t *stream, size_t len, FILE *out)
{
    OI_CODEC_SBC_DECODER_CONTEXT context;
    static OI_UINT32 contextData[1024];
    const char *ns = getenv("SBC_STREAMS");
    OI_STATUS status = OI_CODEC_SBC_DecoderReset(&context, contextData,
                                                 sizeof(contextData), 2,
                                                 (ns && !strcmp(ns, "1")) ? 1 : 2, FALSE);
    if (!OI_SUCCESS(status)) die("sbc DecoderReset failed %d", status);

    const OI_BYTE *ptr = stream;
    OI_UINT32 remain = (OI_UINT32)len;
    int16_t pcmbuf[4096];
    long frames = 0;
    while (remain > 0)
    {
        OI_UINT32 pcmBytes = sizeof(pcmbuf);
        status = OI_CODEC_SBC_DecodeFrame(&context, &ptr, &remain,
                                          (OI_INT16 *)pcmbuf, &pcmBytes);
        if (!OI_SUCCESS(status)) break;   /* tail/padding */
        if (out) fwrite(pcmbuf, 1, pcmBytes, out);
        frames++;
    }
    return frames;
}

static int cmd_dec_sbc(int argc, char **argv)
{
    size_t len;
    uint8_t *stream = read_file(argv[0], &len);
    FILE *f = open_out(argv[1]);
    long frames = sbc_decode_all(stream, len, f);
    fclose(f);
    printf("dec-sbc: %ld frames -> %s\n", frames, argv[1]);
    free(stream);
    return 0;
}

/* ------------------------------------------------------------------- AAC */

static TRANSPORT_TYPE aac_tt(const char *s)
{
    if (!s || !strcmp(s, "latm")) return TT_MP4_LATM_MCP1;
    if (!strcmp(s, "adts")) return TT_MP4_ADTS;
    if (!strcmp(s, "loas")) return TT_MP4_LOAS;
    die("unknown transport %s (latm|adts|loas)", s);
    return TT_UNKNOWN;
}

static int cmd_enc_aac(int argc, char **argv)
{
    size_t plen;
    uint8_t *pcm = read_file(argv[0], &plen);
    const char *outpath = argv[1];
    long bitrate = (argc > 2) ? atol(argv[2]) : 256000;
    TRANSPORT_TYPE tt = aac_tt(argc > 3 ? argv[3] : "latm");

    HANDLE_AACENCODER h = NULL;
    if (aacEncOpen(&h, 0, GEN_CH) != AACENC_OK) die("aacEncOpen failed");
    aacEncoder_SetParam(h, AACENC_AOT, AOT_AAC_LC);
    aacEncoder_SetParam(h, AACENC_SAMPLERATE, GEN_SR);
    aacEncoder_SetParam(h, AACENC_CHANNELMODE, MODE_2);
    aacEncoder_SetParam(h, AACENC_BITRATE, (UINT)bitrate);
    aacEncoder_SetParam(h, AACENC_TRANSMUX, tt);
    aacEncoder_SetParam(h, AACENC_AFTERBURNER, 1);
    /* NOTE: this old FDK-7 encoder build mis-encodes short-window (transient/
       noise/chirp) content -- tonal streams are fine (60dB round-trip). For
       torture streams, generate AAC with ffmpeg on the host instead. */
    aacEncoder_SetParam(h, AACENC_GRANULE_LENGTH, 1024);
    if (aacEncEncode(h, NULL, NULL, NULL, NULL) != AACENC_OK) die("aac enc init failed");

    AACENC_InfoStruct info;
    memset(&info, 0, sizeof(info));
    if (aacEncInfo(h, &info) != AACENC_OK) die("aacEncInfo failed");
    long fl = info.frameLength;         /* samples per channel per frame */
    if (fl <= 0) fl = 1024;

    long nsamples = (long)(plen / (GEN_CH * sizeof(int16_t)));
    long nframes = (nsamples + fl - 1) / fl;
    int16_t *src = (int16_t *)pcm;
    int16_t *stage = (int16_t *)malloc((size_t)fl * GEN_CH * sizeof(int16_t));
    uint8_t obuf[16384];
    FILE *f = open_out(outpath);

    for (long i = 0; i < nframes; i++)
    {
        long off = i * fl * GEN_CH;
        long navail = nsamples * GEN_CH - off;
        long n = fl * GEN_CH;
        if (navail < n) {                 /* zero-pad the final frame */
            memcpy(stage, src + off, (size_t)navail * sizeof(int16_t));
            memset(stage + navail, 0, (size_t)(n - navail) * sizeof(int16_t));
        }
        int16_t *inptr = (navail < n) ? stage : (src + off);

        void *inBuf[1]   = { inptr };
        INT   inBufId[1] = { IN_AUDIO_DATA };
        INT   inSize[1]  = { (INT)(n * sizeof(int16_t)) };
        INT   inEl[1]    = { GEN_CH };
        AACENC_BufDesc inDesc;
        inDesc.numBufs = 1; inDesc.bufs = inBuf;
        inDesc.bufferIdentifiers = inBufId; inDesc.bufSizes = inSize; inDesc.bufElSizes = inEl;

        void *outBuf[1]   = { obuf };
        INT   outBufId[1] = { OUT_BITSTREAM_DATA };
        INT   outSize[1]  = { (INT)sizeof(obuf) };
        INT   outEl[1]    = { 1 };
        AACENC_BufDesc outDesc;
        outDesc.numBufs = 1; outDesc.bufs = outBuf;
        outDesc.bufferIdentifiers = outBufId; outDesc.bufSizes = outSize; outDesc.bufElSizes = outEl;

        AACENC_InArgs inArgs;   memset(&inArgs, 0, sizeof(inArgs));
        AACENC_OutArgs outArgs; memset(&outArgs, 0, sizeof(outArgs));
        inArgs.numInSamples = (INT)n;   /* interleaved samples incl. channels */

        AACENC_ERROR err = aacEncEncode(h, &inDesc, &outDesc, &inArgs, &outArgs);
        if (err != AACENC_OK) die("aacEncEncode frame %ld: %d", i, err);
        if (outArgs.numOutBytes > 0)
            fwrite(obuf, 1, (size_t)outArgs.numOutBytes, f);
    }
    fclose(f);
    aacEncClose(&h);
    printf("enc-aac: %ld frames (%ld Hz, %ld bps, tt=%s) -> %s\n",
           nframes, (long)GEN_SR, bitrate, (tt == TT_MP4_ADTS ? "adts" : "latm"), outpath);
    free(stage); free(pcm);
    return 0;
}

/* shared AAC decode loop -- replicates btif_media_task_handle_inc_aac */
static long aac_decode_all(const uint8_t *stream, size_t len, TRANSPORT_TYPE tt, FILE *out)
{
    HANDLE_AACDECODER dec = aacDecoder_Open(tt, 1);
    if (!dec) die("aacDecoder_Open failed");

    UCHAR *p_payload = (UCHAR *)stream;
    UINT buffer_size = (UINT)len;
    UINT bytes_valid = (UINT)len;      /* glue initializes BOTH to len */
    INT_PCM pcmbuf[4096];
    long frames = 0;

    while (bytes_valid > 0)
    {
        UINT in_size_before = buffer_size;
        long frames_before = frames;
        AAC_DECODER_ERROR err = aacDecoder_Fill(dec, &p_payload, &buffer_size,
                                                &bytes_valid);
        if (err != AAC_DEC_OK)
        {
            fprintf(stderr, "aac Fill failed 0x%x\n", (unsigned)err);
            break;
        }

        while (1)
        {
            err = aacDecoder_DecodeFrame(dec, pcmbuf, 4096, 0 /* flags */);
            if (err == AAC_DEC_NOT_ENOUGH_BITS) break;
            if (err != AAC_DEC_OK)
            {
                fprintf(stderr, "aac DecodeFrame failed 0x%x\n", (unsigned)err);
                bytes_valid = 0;
                break;
            }
            CStreamInfo *info = aacDecoder_GetStreamInfo(dec);
            if (!info || info->frameSize <= 0 || info->numChannels <= 0) break;
            size_t nsmp = (size_t)info->frameSize * (size_t)info->numChannels;
            if (nsmp > 4096) nsmp = 4096;
            if (out) fwrite(pcmbuf, sizeof(INT_PCM), nsmp, out);
            frames++;
        }
        if (buffer_size == in_size_before && frames == frames_before)
        {
            /* neither input consumed nor output produced: hard stall */
            fprintf(stderr, "aac decode stall (in_size=%u frames=%ld)\n",
                    buffer_size, frames);
            break;
        }
    }
    aacDecoder_Close(dec);
    return frames;
}

static int cmd_dec_aac(int argc, char **argv)
{
    size_t len;
    uint8_t *stream = read_file(argv[0], &len);
    TRANSPORT_TYPE tt = aac_tt(argc > 2 ? argv[2] : "latm");
    FILE *f = open_out(argv[1]);
    long frames = aac_decode_all(stream, len, tt, f);
    fclose(f);
    printf("dec-aac: %ld frames -> %s\n", frames, argv[1]);
    free(stream);
    return 0;
}

/* ------------------------------------------------------------------ LDAC */

static int cmd_enc_ldac(int argc, char **argv)
{
    size_t plen;
    uint8_t *pcm = read_file(argv[0], &plen);
    HANDLE_LDAC_BT h = ldacBT_get_handle();
    if (!h) die("ldacBT_get_handle failed");
    if (ldacBT_init_handle_encode(h, 1003, LDACBT_EQMID_SQ,
                                  LDACBT_CHANNEL_MODE_STEREO,
                                  LDACBT_SMPL_FMT_S16, GEN_SR) != 0)
        die("ldacBT_init_handle_encode failed err=%d", ldacBT_get_error_code(h));

    FILE *f = open_out(argv[1]);
    size_t off = 0;
    uint8_t stream[8192];
    long total_frames = 0;
    while (off < plen)
    {
        int pcm_used = 0, stream_sz = 0, frame_num = 0;
        if (ldacBT_encode(h, pcm + off, &pcm_used, stream, &stream_sz, &frame_num) != 0)
            die("ldacBT_encode failed err=%d", ldacBT_get_error_code(h));
        if (stream_sz > 0)
        {
            fwrite(stream, 1, (size_t)stream_sz, f);
            total_frames += frame_num;
        }
        if (pcm_used <= 0) break;
        off += (size_t)pcm_used;
    }
    /* flush */
    int pcm_used = 0, stream_sz = 0, frame_num = 0;
    if (ldacBT_encode(h, NULL, &pcm_used, stream, &stream_sz, &frame_num) == 0 && stream_sz > 0)
    {
        fwrite(stream, 1, (size_t)stream_sz, f);
        total_frames += frame_num;
    }
    fclose(f);
    ldacBT_close_handle(h);
    ldacBT_free_handle(h);
    printf("enc-ldac: %ld transport frames -> %s\n", total_frames, argv[1]);
    free(pcm);
    return 0;
}

/* shared LDAC decode loop -- replicates btif_media_task_handle_inc_ldac */
static long ldac_decode_all(const uint8_t *stream, size_t len, FILE *out)
{
    static ldacdec_t dec;   /* ~150KB struct: keep off the stack */
    if (ldacdecInit(&dec) != 0) die("ldacdecInit failed");
    uint8_t *p = (uint8_t *)stream;
    size_t remain = len;
    int16_t pcm[MAX_FRAME_SAMPLES * 2];
    long frames = 0;
    while (remain > 0)
    {
        int used = 0;
        int ret = ldacDecode(&dec, p, pcm, &used);
        if (ret != 0 || used <= 0)
        {
            fprintf(stderr, "harness: ldacDecode failed ret=%d used=%d rem=%zu\n",
                    ret, used, remain);
            break;
        }
        int ns = dec.frame.frameSamples;
        int nch = dec.frame.channelCount;
        if (ns <= 0 || ns > MAX_FRAME_SAMPLES || nch <= 0 || nch > 2)
            die("ldac bad frame geometry ns=%d nch=%d", ns, nch);
        if (out) fwrite(pcm, sizeof(int16_t), (size_t)ns * nch, out);
        p += used;
        remain -= (size_t)used;
        frames++;
    }
    return frames;
}

static int cmd_dec_ldac(int argc, char **argv)
{
    size_t len;
    uint8_t *stream = read_file(argv[0], &len);
    FILE *f = open_out(argv[1]);
    long frames = ldac_decode_all(stream, len, f);
    fclose(f);
    printf("dec-ldac: %ld frames -> %s\n", frames, argv[1]);
    free(stream);
    return 0;
}

/* ------------------------------------------------------------------ psnr */

static int cmd_psnr(int argc, char **argv)
{
    size_t la, lb;
    uint8_t *a = read_file(argv[0], &la);
    uint8_t *b = read_file(argv[1], &lb);
    double thr = (argc > 2) ? atof(argv[2]) : 1e9;   /* dB threshold */

    if (la != lb)
    {
        /* Frame-boundary truncation by the encoder is normal; compare the
           common prefix and note it. Hard-fail only if almost nothing overlaps. */
        printf("psnr: note: lengths differ %zu vs %zu, comparing first %zu bytes\n",
               la, lb, la < lb ? la : lb);
        if (la < lb) lb = la; else la = lb;
        if (la < 4096) return 1;
    }
    const int16_t *sa0 = (const int16_t *)a, *sb0 = (const int16_t *)b;
    size_t n0 = la / sizeof(int16_t);

    /* Codec round-trips introduce a fixed filter delay (~736 interleaved
       samples for SBC here). Find the integer lag L minimizing
       sum (sa0[i+L]-sb0[i])^2 over [-4096..4096] (sb0 delayed by L).
       Identical files (o0 vs o2 decode) hit L=0 with zero error instantly. */
    long bestLag = 0;
    {
        double bestErr = 1e300;
        for (long L = -8192; L <= 8192; L++)
        {
            /* probe window must clear the decoder priming zeros (AAC decodes
               start with ~2000 interleaved samples of silence): 16384 is safe
               for all three codecs here */
            long i0 = (L >= 0 ? L : 0) + 20480;
            long i1 = (long)n0 - 20480;
            if (i1 - i0 < 1000) continue;
            double e = 0.0;
            long cnt = 0;
            for (long i = i0; i < i1 && cnt < 4000; i += 16, cnt++)
            {
                double d = (double)sa0[i] - (double)sb0[i - L];
                e += d * d;
            }
            if (e < bestErr) { bestErr = e; bestLag = L; }
            if (bestErr == 0.0) break;
        }
    }
    if (bestLag != 0) printf("psnr: aligned at lag %ld samples\n", bestLag);
    const int16_t *sa, *sb;
    size_t n, skip;
    if (bestLag >= 0) { sa = sa0 + bestLag; sb = sb0; skip = (size_t)bestLag; }
    else              { sa = sa0; sb = sb0 - bestLag; skip = (size_t)(-bestLag); }
    n = (n0 > skip) ? (n0 - skip) : 0;
    /* Skip the decoder priming region (AAC: ~4096 interleaved samples of
       filter-delay zeros; SBC ~736; LDAC ~1138): 16384 covers all. No effect
       on same-stream o0-vs-o2 comparisons. */
    if (n > 40000) { sa += 16384; sb += 16384; n -= 16384; }
    if (n < 16) { printf("psnr: files too short to compare\n"); return 2; }

    double sig = 0.0, err = 0.0;
    int maxabs = 0;
    size_t mism = 0, first = (size_t)-1;
    for (size_t i = 0; i < n; i++)
    {
        int d = (int)sa[i] - (int)sb[i];
        if (d != 0) { mism++; if (first == (size_t)-1) first = i; }
        if (abs(d) > maxabs) maxabs = abs(d);
        sig += (double)sa[i] * sa[i];
        err += (double)d * d;
    }
    if (mism == 0)
    {
        printf("psnr: BIT-EXACT (%zu samples)\n", n);
        return 0;
    }
    double psnr = (err == 0.0) ? 1e9 : 10.0 * log10(sig / err);
    printf("psnr: %zu/%zu samples differ, maxabs=%d, first@%zu, PSNR=%.2f dB\n",
           mism, n, maxabs, first, psnr);
    free(a); free(b);
    return (psnr >= thr) ? 0 : 1;
}

/* ----------------------------------------------------------------- bench */

static int cmd_bench(int argc, char **argv)
{
    const char *codec = argv[0];
    const char *path = argv[1];
    long iters = (argc > 2) ? atol(argv[2]) : 10;
    TRANSPORT_TYPE tt = aac_tt(argc > 3 ? argv[3] : "latm");
    size_t len;
    uint8_t *stream = read_file(path, &len);

    /* one reference pass to learn the frame count */
    long frames = 0;
    double t0 = now_sec();
    if (!strcmp(codec, "sbc"))       frames = sbc_decode_all(stream, len, NULL);
    else if (!strcmp(codec, "aac"))  frames = aac_decode_all(stream, len, tt, NULL);
    else if (!strcmp(codec, "ldac")) frames = ldac_decode_all(stream, len, NULL);
    else die("bench codec %s unknown", codec);
    double one = now_sec() - t0;
    if (frames <= 0) die("no frames decoded");

    for (long i = 1; i < iters; i++)
    {
        if (!strcmp(codec, "sbc"))       sbc_decode_all(stream, len, NULL);
        else if (!strcmp(codec, "aac"))  aac_decode_all(stream, len, tt, NULL);
        else                             ldac_decode_all(stream, len, NULL);
    }
    double total = now_sec() - t0;
    double per_frame_ns = total * 1e9 / ((double)frames * iters);
    /* LDAC frame = 128 samples, SBC(16blk x 8sb) = 128, AAC = 1024:
     * report realtime factor using 44.1 kHz */
    double frame_sec = (!strcmp(codec, "aac") ? 1024.0 : 128.0) / 44100.0;
    printf("bench-%s: %ld frames x %ld iters, %.3fs total, %.0f ns/frame, "
           "realtime factor %.1fx\n",
           codec, frames, iters, total, per_frame_ns,
           frame_sec / (per_frame_ns * 1e-9));
    (void)one;
    free(stream);
    return 0;
}

/* ------------------------------------------------------------------ main */

static void usage(void)
{
    fprintf(stderr,
        "usage: harness <mode> ...\n"
        "  gen      <out.pcm> [secs=6] [sr=44100]\n"
        "  enc-sbc  <in.pcm> <out.sbc>\n"
        "  dec-sbc  <in.sbc> <out.pcm>\n"
        "  enc-aac  <in.pcm> <out.aac> [bitrate=256000] [tt=latm|adts|loas]\n"
        "  dec-aac  <in.aac> <out.pcm> [tt=latm|adts|loas]\n"
        "  enc-ldac <in.pcm> <out.ldac>\n"
        "  dec-ldac <in.ldac> <out.pcm>\n"
        "  psnr     <a.pcm> <b.pcm> [thr_db]   exit0 iff bit-exact or psnr>=thr\n"
        "  bench    <sbc|aac|ldac> <stream> [iters=10] [latm|adts|loas]\n");
    exit(2);
}

int main(int argc, char **argv)
{
    if (argc < 2) usage();
    const char *m = argv[1];
    argc -= 2; argv += 2;

    struct { const char *name; int min; int (*fn)(int, char **); } cmds[] = {
        { "gen",      1, cmd_gen      },
        { "enc-sbc",  2, cmd_enc_sbc  },
        { "dec-sbc",  2, cmd_dec_sbc  },
        { "enc-aac",  2, cmd_enc_aac  },
        { "dec-aac",  2, cmd_dec_aac  },
        { "enc-ldac", 2, cmd_enc_ldac },
        { "dec-ldac", 2, cmd_dec_ldac },
        { "psnr",     2, cmd_psnr     },
        { "bench",    2, cmd_bench    },
    };
    for (unsigned i = 0; i < sizeof(cmds) / sizeof(cmds[0]); i++)
        if (!strcmp(m, cmds[i].name))
        {
            if (argc < cmds[i].min) usage();
            return cmds[i].fn(argc, argv);
        }
    usage();
    return 2;
}
