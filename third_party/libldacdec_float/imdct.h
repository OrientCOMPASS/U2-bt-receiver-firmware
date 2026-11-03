#pragma once

/*
 * libldacdec float+NEON variant (Phase B of the decode power optimization).
 * Mdct state/tables are single-precision float (was double): the LDAC output
 * is 16-bit PCM, so float (24-bit mantissa) leaves >=8 bits of headroom over
 * the quantization floor; measured old-vs-new PSNR stays >90 dB.
 */
typedef struct {
	int Bits;
	int Size;
	float Scale;
	float ImdctPrevious[MAX_FRAME_SAMPLES];
	float* Window;
	float* SinTable;
	float* CosTable;
} Mdct;

void InitMdct();
void RunImdct(Mdct* mdct, float* input, float* output);
