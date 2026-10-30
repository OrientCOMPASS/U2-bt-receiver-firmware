# libldacdec (vendored core, MIT)

Clean-room LDAC decoder by Dirk Helbig (https://github.com/hegdi/libldacdec,
MIT License, (c) 2021).  LDAC is "a stripped down, streaming only ATRAC9".

Vendored subset = decoder core only (upstream @ 7f3bd6c, 2026-10-30):
bit_allocation / bit_reader / huffCodes / imdct / libldacdec / spectrum /
utility + headers.  Excluded: CLI tools (ldacdec.c, ldacenc.c), the Android
encoder submodule (libldac/), host Makefile.

Usage in this project: statically linked into our Nougat bluetooth.default.so
by scripts/bt_standalone_build.sh (root key `libldacdec`, -std=gnu99 for the
min/max statement-expression macros in ldacdec.h).  The A2DP LDAC sink glue
lives in patches/nougat (a2d_ldac.c + btif_media_task.c decode branch).

Public API (libldacdec.c / ldacdec.h):
  ldacdec_t dec; ldacdecInit(&dec);
  int ldacDecode(&dec, uint8_t *stream, int16_t *pcm, int *bytesUsed);
  int ldacdecGetSampleRate(&dec);   // 44100/48000/88200/96000
  int ldacdecGetChannelCount(&dec); // 1 or 2
  int ldacNullPacket(&dec, uint8_t *out, int *bytesUsed); // PLC

NOTE: "LDAC" is a trademark of Sony Group Corp.  This project is
LDAC-*compatible* only; no Sony certification or license is implied.
