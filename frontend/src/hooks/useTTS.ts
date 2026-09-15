import { useCallback, useEffect, useRef, useState } from 'react';
import { synthesizeSpeech } from '../lib/api';
import { useAppStore } from '../lib/store';

export function useTTS() {
  const [ready, setReady] = useState(false);
  const [audioUnlocked, setAudioUnlocked] = useState(false);
  const setVoicePlaybackState = useAppStore((state) => state.setVoicePlaybackState);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const pendingAudioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    // Backend TTS is always ready once the server is reachable.
    setReady(true);

    // Unlock audio playback after the first user gesture. Browsers block
    // autoplay until the user interacts with the document; a silent audio
    // click primes the element so voice responses can play after wake words.
    const unlock = async () => {
      try {
        const silent = new Audio('data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQIAAAAAAA==');
        silent.volume = 0;
        await silent.play();
        setAudioUnlocked(true);
      } catch {
        // Some browsers still block; the next explicit click will retry.
      }
    };
    const events = ['click', 'keydown', 'touchstart'];
    events.forEach((event) => window.addEventListener(event, unlock, { once: true }));
    return () => {
      events.forEach((event) => window.removeEventListener(event, unlock));
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
      pendingAudioRef.current = null;
      setVoicePlaybackState('idle');
    };
  }, [setVoicePlaybackState]);

  // When audio becomes unlocked, resume any TTS that was blocked by autoplay.
  useEffect(() => {
    if (audioUnlocked && pendingAudioRef.current) {
      void pendingAudioRef.current.play().catch(() => {});
      pendingAudioRef.current = null;
    }
  }, [audioUnlocked]);

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    pendingAudioRef.current = null;
    setVoicePlaybackState('idle');
  }, [setVoicePlaybackState]);

  const speak = useCallback((text: string) => {
    if (!text.trim()) return;

    const clean = text
      .replace(/\p{Extended_Pictographic}/gu, '')
      .replace(/[\u200d\ufe0e\ufe0f]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!clean) return;

    stop();
    setVoicePlaybackState('loading');

    void synthesizeSpeech(clean)
      .then((blob) => {
        if (urlRef.current) {
          URL.revokeObjectURL(urlRef.current);
          urlRef.current = null;
        }
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => setVoicePlaybackState('idle');
        audio.onerror = (event) => {
          console.error('TTS audio element error:', event);
          setVoicePlaybackState('idle');
        };
        setVoicePlaybackState('speaking');
        void audio.play().then(() => {
          pendingAudioRef.current = null;
        }).catch((err) => {
          if (err?.name === 'NotAllowedError') {
            // Browser autoplay policy blocked playback. Queue the audio so
            // it plays as soon as the user interacts with the page.
            pendingAudioRef.current = audio;
            console.warn(
              'TTS autoplay blocked; audio will resume after the next user interaction.'
            );
          } else {
            console.error('TTS playback failed:', err?.name ?? 'unknown', err?.message ?? err);
          }
          setVoicePlaybackState('idle');
        });
      })
      .catch((err) => {
        setVoicePlaybackState('idle');
        console.error('TTS synthesis failed:', err);
      });
  }, [setVoicePlaybackState, stop]);

  return { speak, stop, ready };
}
