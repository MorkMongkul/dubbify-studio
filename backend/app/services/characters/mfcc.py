"""MFCCs with numpy only (the app doesn't ship scipy), identical to python_speech_features.mfcc.

Light-ASD was trained on python_speech_features' MFCCs, so this follows its defaults exactly: 25 ms frames every
10 ms with a rectangular window, pre-emphasis 0.97, 512-point FFT, 26 mel filters from 0 Hz to rate/2, DCT-II with
orthonormal scaling keeping 13 coefficients, liftering 22, and the first coefficient replaced by the log frame energy.
`tests/fixtures/mfcc_reference.npz` (written by scripts/export-light-asd.py) is the check."""
from functools import lru_cache

import numpy as np

NUM_CEP = 13
NFILT = 26
NFFT = 512
PREEMPH = 0.97
CEPLIFTER = 22
WINLEN = 0.025
WINSTEP = 0.010


def _hz_to_mel(hz):
    return 2595 * np.log10(1 + np.asarray(hz, dtype=np.float64) / 700.0)


def _mel_to_hz(mel):
    return 700 * (10 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1)


@lru_cache(maxsize=4)
def _filterbank(rate: int) -> np.ndarray:
    """Triangular mel filters, shape (NFILT, NFFT // 2 + 1)."""
    points = np.linspace(_hz_to_mel(0), _hz_to_mel(rate / 2), NFILT + 2)
    bins = np.floor((NFFT + 1) * _mel_to_hz(points) / rate)
    bank = np.zeros((NFILT, NFFT // 2 + 1))
    for j in range(NFILT):
        for i in range(int(bins[j]), int(bins[j + 1])):
            bank[j, i] = (i - bins[j]) / (bins[j + 1] - bins[j])
        for i in range(int(bins[j + 1]), int(bins[j + 2])):
            bank[j, i] = (bins[j + 2] - i) / (bins[j + 2] - bins[j + 1])
    return bank


@lru_cache(maxsize=1)
def _dct_matrix() -> np.ndarray:
    """DCT-II, orthonormal, keeping NUM_CEP rows: (NUM_CEP, NFILT)."""
    n = np.arange(NFILT)
    k = np.arange(NUM_CEP)[:, None]
    matrix = np.cos(np.pi * k * (2 * n + 1) / (2 * NFILT)) * np.sqrt(2.0 / NFILT)
    matrix[0] /= np.sqrt(2.0)
    return matrix


def mfcc(signal: np.ndarray, rate: int = 16000) -> np.ndarray:
    """MFCCs of a mono signal (int16 or float, on the int16 scale), shape (frames, 13)."""
    signal = np.asarray(signal, dtype=np.float64)
    signal = np.append(signal[0], signal[1:] - PREEMPH * signal[:-1])
    frame_len, frame_step = int(round(WINLEN * rate)), int(round(WINSTEP * rate))
    frames = 1 if len(signal) <= frame_len else 1 + int(np.ceil((1.0 * len(signal) - frame_len) / frame_step))
    padded = np.concatenate([signal, np.zeros((frames - 1) * frame_step + frame_len - len(signal))])
    index = np.arange(frame_len)[None, :] + (np.arange(frames) * frame_step)[:, None]
    power = np.square(np.absolute(np.fft.rfft(padded[index], NFFT))) / NFFT
    energy = power.sum(axis=1)
    energy = np.where(energy == 0, np.finfo(float).eps, energy)
    bands = power @ _filterbank(rate).T
    bands = np.where(bands == 0, np.finfo(float).eps, bands)
    cep = np.log(bands) @ _dct_matrix().T
    lift = 1 + (CEPLIFTER / 2.0) * np.sin(np.pi * np.arange(NUM_CEP) / CEPLIFTER)
    cep = cep * lift
    cep[:, 0] = np.log(energy)
    return cep
