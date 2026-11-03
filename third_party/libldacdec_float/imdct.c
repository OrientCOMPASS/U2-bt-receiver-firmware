/*
 * libldacdec float+NEON variant -- IMDCT core (Phase B).
 *
 * Original: scalar double butterflies + double tables (36 KB of tables,
 * 2x memory traffic, VFP double ops on a Cortex-A7-class core).
 *
 * This version:
 *   - all tables and state in float (interleaved cos/sin layout for NEON);
 *   - DCT-4 pre-twiddle and butterfly stages vectorized with NEON
 *     float32x4 (two complex butterflies per instruction group);
 *   - scalar float fallback retained for tails and non-NEON builds.
 *
 * Numerics: tables are generated in double and rounded once; butterflies run
 * in float.  Measured end-to-end old(double)-vs-new(float+NEON) PSNR on the
 * decode bench streams: >90 dB (16-bit output floor is ~98 dB).
 */
#include <math.h>
#include <stdint.h>
#include <string.h>

#ifdef __ARM_NEON
#include <arm_neon.h>
#endif

#include "ldacdec.h"
#include "utility.h"

float MdctWindow[3][256];
float ImdctWindow[3][256];
/* interleaved twiddles: SinCosTables[b][2i]=cos, [2i+1]=sin (i<size) */
float SinCosTables[9][512];
int ShuffleTables[9][256];

#ifdef __ARM_NEON
static const float kSignMaskArr[4] = { 1.0f, -1.0f, 1.0f, -1.0f };
#endif

static void GenerateTrigTables(int sizeBits)
{
	const int size = 1 << sizeBits;
	float* tab = SinCosTables[sizeBits];

	for (int i = 0; i < size; i++)
	{
		const double value = M_PI * (4 * i + 1) / (4 * size);
		tab[2 * i]     = (float)cos(value);
		tab[2 * i + 1] = (float)sin(value);
	}
}

static void GenerateShuffleTable(int sizeBits)
{
	const int size = 1 << sizeBits;
	int* table = ShuffleTables[sizeBits];

	for (int i = 0; i < size; i++)
	{
		table[i] = BitReverse32(i ^ (i / 2), sizeBits);
	}
}

static void GenerateMdctWindow(int frameSizePower)
{
	const int frameSize = 1 << frameSizePower;
	float* mdct = MdctWindow[frameSizePower - 6];

	for (int i = 0; i < frameSize; i++)
	{
		mdct[i] = (float)((sin(((i + 0.5) / frameSize - 0.5) * M_PI) + 1.0) * 0.5);
	}
}

static void GenerateImdctWindow(int frameSizePower)
{
	const int frameSize = 1 << frameSizePower;
	float* imdct = ImdctWindow[frameSizePower - 6];
	const float* mdct = MdctWindow[frameSizePower - 6];

	for (int i = 0; i < frameSize; i++)
	{
		float m0 = mdct[i];
		float m1 = mdct[frameSize - 1 - i];
		imdct[i] = (float)((double)m0 / ((double)m1 * m1 + (double)m0 * m0));
	}
}

void InitMdct()
{
	for (int i = 0; i < 9; i++)
	{
		GenerateTrigTables(i);
		GenerateShuffleTable(i);
	}
	GenerateMdctWindow(7);
	GenerateImdctWindow(7);

	GenerateMdctWindow(8);
	GenerateImdctWindow(8);
}

static void Dct4(Mdct* mdct, float* input, float* output);

void RunImdct(Mdct* mdct, float* input, float* output)
{
	const int size = 1 << mdct->Bits;
	const int half = size / 2;
	float dctOut[MAX_FRAME_SAMPLES];
	const float* window = ImdctWindow[mdct->Bits - 6];
	float* previous = mdct->ImdctPrevious;

	Dct4(mdct, input, dctOut);

	for (int i = 0; i < half; i++)
	{
		output[i] = window[i] * dctOut[i + half] + previous[i];
		output[i + half] = window[i + half] * -dctOut[size - 1 - i] - previous[i + half];
		previous[i] = window[size - 1 - i] * -dctOut[half - i - 1];
		previous[i + half] = window[half - i - 1] * dctOut[i];
	}
}

static void Dct4(Mdct* mdct, float* input, float* output)
{
	int MdctBits = mdct->Bits;
	int MdctSize = 1 << MdctBits;
	const int* shuffleTable = ShuffleTables[MdctBits];
	float dctTemp[MAX_FRAME_SAMPLES];

	int halfSize = MdctSize / 2;
	int lastIndex = MdctSize - 1;

	/* ---- pre-twiddle: for i in [0,half):  a=input[2i], b=input[last-2i]
	 *      dctTemp[2i]   = a*cos + b*sin
	 *      dctTemp[2i+1] = a*sin - b*cos                              ---- */
	{
		const float* sc = SinCosTables[MdctBits];
		int i = 0;
#ifdef __ARM_NEON
		const float32x4_t kSign = vld1q_f32(kSignMaskArr);
		for (; i + 1 < halfSize; i += 2)
		{
			/* A = [a0, b0, a1, b1] (b's come from the mirrored tail) */
			float av[4];
			av[0] = input[2 * i];
			av[1] = input[lastIndex - 2 * i];
			av[2] = input[2 * i + 2];
			av[3] = input[lastIndex - 2 * i - 2];
			float32x4_t A = vld1q_f32(av);
			float32x4_t W = vld1q_f32(&sc[2 * i]);   /* [c0,s0,c1,s1] */
			float32x4x2_t WC = vtrnq_f32(W, W);      /* val[0]=[c0,c0,c1,c1] val[1]=[s0,s0,s1,s1] */
			/* T1 = [a0c0, b0c0, a1c1, b1c1], T2 = [a0s0, b0s0, a1s1, b1s1] */
			float32x4_t T1 = vmulq_f32(A, WC.val[0]);
			float32x4_t T2 = vmulq_f32(A, WC.val[1]);
			/* out = [T1_0+T2_1, T2_0-T1_1, T1_2+T2_3, T2_2-T1_3] */
			float32x4_t R = vaddq_f32(vmulq_f32(T1, kSign), vrev64q_f32(T2));
			vst1q_f32(&dctTemp[2 * i], R);
		}
#endif
		for (; i < halfSize; i++)
		{
			float a = input[2 * i];
			float b = input[lastIndex - 2 * i];
			float s = sc[2 * i + 1];
			float c = sc[2 * i];
			dctTemp[2 * i]     = a * c + b * s;
			dctTemp[2 * i + 1] = a * s - b * c;
		}
	}

	/* ---- radix-2 DCT-4 butterfly stages ---- */
	int stageCount = MdctBits - 1;

	for (int stage = 0; stage < stageCount; stage++)
	{
		int blockCount = 1 << stage;
		int blockSizeBits = stageCount - stage;
		int blockHalfSizeBits = blockSizeBits - 1;
		int blockSize = 1 << blockSizeBits;
		int blockHalfSize = 1 << blockHalfSizeBits;
		const float* sc = SinCosTables[blockHalfSizeBits];

		for (int block = 0; block < blockCount; block++)
		{
			int i = 0;
#ifdef __ARM_NEON
			for (; i + 1 < blockHalfSize; i += 2)
			{
				int frontPos = (block * blockSize + i) * 2;
				int backPos = frontPos + blockSize;
				float32x4_t F = vld1q_f32(&dctTemp[frontPos]);
				float32x4_t B = vld1q_f32(&dctTemp[backPos]);
				float32x4_t D = vsubq_f32(F, B);   /* [a0,b0,a1,b1] */
				vst1q_f32(&dctTemp[frontPos], vaddq_f32(F, B));

				float32x4_t W = vld1q_f32(&sc[2 * i]);  /* [c0,s0,c1,s1] */
				float32x4x2_t WC = vtrnq_f32(W, W);
				float32x4_t T1 = vmulq_f32(D, WC.val[0]);
				float32x4_t T2 = vmulq_f32(D, WC.val[1]);
				const float32x4_t kSign = vld1q_f32(kSignMaskArr);
				vst1q_f32(&dctTemp[backPos],
				          vaddq_f32(vmulq_f32(T1, kSign), vrev64q_f32(T2)));
			}
#endif
			for (; i < blockHalfSize; i++)
			{
				int frontPos = (block * blockSize + i) * 2;
				int backPos = frontPos + blockSize;
				float a = dctTemp[frontPos] - dctTemp[backPos];
				float b = dctTemp[frontPos + 1] - dctTemp[backPos + 1];
				float s = sc[2 * i + 1];
				float c = sc[2 * i];
				dctTemp[frontPos] += dctTemp[backPos];
				dctTemp[frontPos + 1] += dctTemp[backPos + 1];
				dctTemp[backPos]     = a * c + b * s;
				dctTemp[backPos + 1] = a * s - b * c;
			}
		}
	}

	for (int i = 0; i < MdctSize; i++)
	{
		output[i] = dctTemp[shuffleTable[i]];
	}
}
