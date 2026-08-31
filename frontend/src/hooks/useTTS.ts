import { useCallback, useEffect, useRef, useState } from 'react';
import { synthesizeSpeech } from '../lib/api';

export function useTTS() {
  const [ready, setReady] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    // Backend TTS is always ready once the server is reachable.
    setReady(true);
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };
  }, []);

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
  }, []);

  const speak = useCallback((text: string) => {
    if (!text.trim()) return;

    const clean = text
      .replace(/\p{Extended_Pictographic}/gu, '')
      .replace(/[\u200d\ufe0e\ufe0f]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
    if (!clean) return;

    stop();

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
        void audio.play();
      })
      .catch((err) => {
        console.error('TTS synthesis failed:', err);
      });
  }, [stop]);

  return { speak, stop, ready };
}
