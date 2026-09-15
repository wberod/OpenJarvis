import { useEffect } from 'react';

function playSilent() {
  const audio = new Audio('data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAAAAAA==');
  audio.volume = 0.001;
  void audio.play().catch(() => {});
}

export function useAudioUnlock() {
  useEffect(() => {
    let unlocked = false;

    const handler = () => {
      if (unlocked) return;
      unlocked = true;
      playSilent();
      document.removeEventListener('pointerdown', handler);
      document.removeEventListener('keydown', handler);
    };

    document.addEventListener('pointerdown', handler, { passive: true });
    document.addEventListener('keydown', handler, { passive: true });

    return () => {
      document.removeEventListener('pointerdown', handler);
      document.removeEventListener('keydown', handler);
    };
  }, []);
}
