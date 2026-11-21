//! Запись речи с микрофона через cpal → WAV 16 кГц mono для DeepSeek ASR (кнопка 🎤).
use anyhow::{anyhow, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use parking_lot::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub fn record_wav_16k(stop: Arc<AtomicBool>, max_secs: u64) -> Result<Vec<u8>> {
    let host = cpal::default_host();
    let device = host.default_input_device().ok_or_else(|| anyhow!("Микрофон не найден"))?;
    let cfg = device.default_input_config()?;
    let in_sr = cfg.sample_rate().0;
    let channels = cfg.channels() as usize;

    let buf: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::with_capacity(in_sr as usize * 10)));
    let buf_c = buf.clone();
    let heard = Arc::new(AtomicBool::new(false));
    let heard_c = heard.clone();
    let last_voice = Arc::new(Mutex::new(Instant::now()));
    let lv_c = last_voice.clone();

    let stream = device.build_input_stream(
        &cfg.into(),
        move |data: &[f32], _| {
            let mut mono = Vec::with_capacity(data.len() / channels.max(1));
            let mut rms_sum = 0.0f32;
            for frame in data.chunks(channels.max(1)) {
                let s = frame.iter().sum::<f32>() / channels.max(1) as f32;
                rms_sum += s * s;
                mono.push(s);
            }
            let rms = (rms_sum / mono.len().max(1) as f32).sqrt();
            if rms > 0.015 {
                heard_c.store(true, Ordering::Relaxed);
                *lv_c.lock() = Instant::now();
            }
            buf_c.lock().extend_from_slice(&mono);
        },
        |_| {},
        None,
    )?;

    stream.play()?;
    let t0 = Instant::now();
    while !stop.load(Ordering::Relaxed) && t0.elapsed() < Duration::from_secs(max_secs) {
        std::thread::sleep(Duration::from_millis(50));
        if heard.load(Ordering::Relaxed) && last_voice.lock().elapsed() > Duration::from_millis(1600) {
            break;
        }
        if !heard.load(Ordering::Relaxed) && t0.elapsed() > Duration::from_secs(8) {
            return Err(anyhow!("Не услышала речь — проверь микрофон."));
        }
    }
    drop(stream);

    if !heard.load(Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    let samples = buf.lock().clone();
    // Ресемплинг в 16000 Гц
    let ratio = 16000.0 / in_sr as f64;
    let out_len = (samples.len() as f64 * ratio) as usize;
    let mut pcm = Vec::with_capacity(out_len * 2);
    for i in 0..out_len {
        let src = i as f64 / ratio;
        let idx = src.floor() as usize;
        let frac = (src - idx as f64) as f32;
        let a = samples.get(idx).copied().unwrap_or(0.0);
        let b = samples.get(idx + 1).copied().unwrap_or(a);
        let s = ((a + (b - a) * frac).clamp(-1.0, 1.0) * 32767.0) as i16;
        pcm.extend_from_slice(&s.to_le_bytes());
    }

    // Заголовок WAV (16 кГц, 16-bit, mono)
    let data_len = pcm.len() as u32;
    let mut wav = Vec::with_capacity(44 + pcm.len());
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + data_len).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());      // PCM
    wav.extend_from_slice(&1u16.to_le_bytes());      // mono
    wav.extend_from_slice(&16000u32.to_le_bytes());  // sample rate
    wav.extend_from_slice(&32000u32.to_le_bytes());  // byte rate
    wav.extend_from_slice(&2u16.to_le_bytes());      // block align
    wav.extend_from_slice(&16u16.to_le_bytes());     // bits per sample
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_len.to_le_bytes());
    wav.extend_from_slice(&pcm);
    Ok(wav)
}
