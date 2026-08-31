import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';
import { useAppStore } from '../lib/store';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

const SILENCE_THRESHOLD = 0.05;
const SILENCE_DURATION_MS = 5000;

export function useSpeech() {
  const [state, setState] = useState<SpeechState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const onTranscriptRef = useRef<((text: string) => void) | null>(null);
  const deferredRef = useRef<{ resolve: (text: string) => void; reject: (err: Error) => void } | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastVoiceRef = useRef<number>(Date.now());

  // Check if speech backend is available on mount and sync enabled state
  useEffect(() => {
    fetchSpeechHealth()
      .then((health) => {
        setAvailable(health.available);
        useAppStore.getState().updateSettings({ speechEnabled: health.enabled !== false });
      })
      .catch(() => setAvailable(false));
  }, []);

  const cleanupAudio = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (analyserRef.current) {
      analyserRef.current.disconnect();
      analyserRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      void audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
  }, []);

  const processRecording = useCallback(async () => {
    setState('transcribing');
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    cleanupAudio();

    const recorder = mediaRecorderRef.current;
    const blob = new Blob(chunksRef.current, { type: recorder?.mimeType || 'audio/webm' });
    chunksRef.current = [];
    mediaRecorderRef.current = null;

    try {
      const result = await transcribeAudio(blob);
      setState('idle');
      if (onTranscriptRef.current) {
        onTranscriptRef.current(result.text);
        onTranscriptRef.current = null;
      } else if (deferredRef.current) {
        deferredRef.current.resolve(result.text);
        deferredRef.current = null;
      }
    } catch (err) {
      setState('idle');
      const msg = err instanceof Error ? err.message : 'Transcription failed';
      setError(msg);
      if (deferredRef.current) {
        deferredRef.current.reject(new Error(msg));
        deferredRef.current = null;
      }
      onTranscriptRef.current = null;
    }
  }, [cleanupAudio]);

  const startRecording = useCallback(async (): Promise<void> => {
    setError(null);
    onTranscriptRef.current = null;
    deferredRef.current = null;

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported in this browser');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        void processRecording();
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setState('recording');
    } catch (err) {
      setError('Microphone access denied');
      setState('idle');
    }
  }, [processRecording]);

  const startHandsFree = useCallback(
    async (onTranscript: (text: string) => void): Promise<void> => {
      setError(null);
      onTranscriptRef.current = onTranscript;
      deferredRef.current = null;

      if (!navigator.mediaDevices?.getUserMedia) {
        setError('Microphone not supported in this browser');
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;

        const recorder = new MediaRecorder(stream);
        chunksRef.current = [];

        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) chunksRef.current.push(e.data);
        };
        recorder.onstop = () => {
          void processRecording();
        };

        recorder.start();
        mediaRecorderRef.current = recorder;

        const audioCtx = new AudioContext();
        audioCtxRef.current = audioCtx;
        const source = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 2048;
        source.connect(analyser);
        analyserRef.current = analyser;

        const dataArray = new Uint8Array(analyser.fftSize);
        lastVoiceRef.current = Date.now();

        const checkSilence = () => {
          if (!analyserRef.current || !mediaRecorderRef.current) return;
          analyser.getByteTimeDomainData(dataArray);
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) {
            const v = (dataArray[i] - 128) / 128.0;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / dataArray.length);
          if (rms > SILENCE_THRESHOLD) {
            lastVoiceRef.current = Date.now();
          } else if (Date.now() - lastVoiceRef.current > SILENCE_DURATION_MS) {
            recorder.stop();
            return;
          }
          rafRef.current = requestAnimationFrame(checkSilence);
        };

        rafRef.current = requestAnimationFrame(checkSilence);
        setState('recording');
      } catch (err) {
        setError('Microphone access denied');
        setState('idle');
      }
    },
    [processRecording],
  );

  const stopRecording = useCallback(async (): Promise<string> => {
    return new Promise((resolve, reject) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state !== 'recording') {
        reject(new Error('Not recording'));
        return;
      }

      onTranscriptRef.current = null;
      deferredRef.current = { resolve, reject };
      recorder.stop();
    });
  }, []);

  return {
    state,
    error,
    available,
    startRecording,
    startHandsFree,
    stopRecording,
    isRecording: state === 'recording',
    isTranscribing: state === 'transcribing',
  };
}
