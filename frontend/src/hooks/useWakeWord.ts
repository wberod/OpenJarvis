import { useEffect, useState } from 'react';
import { fetchWakeStatus, setWakeListening, wakeEventsUrl, type WakeEvent } from '../lib/api';
import { useAppStore } from '../lib/store';

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionErrorLike {
  error: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

interface SpeechRecognitionWindow extends Window {
  SpeechRecognition?: new () => SpeechRecognitionLike;
  webkitSpeechRecognition?: new () => SpeechRecognitionLike;
}

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function wordPattern(word: string) {
  const safe = escapeRegex(word);
  // For short wake words, also match when letters are spaced out by speech
  // engines (e.g. "S C" in addition to "SC").
  if (word.length <= 3) {
    const spaced = safe.split('').join('\\s*');
    return `(?:${spaced}|${safe})`;
  }
  return safe;
}

function buildWakePattern(phrase = 'hey sc') {
  // Build a case-insensitive regex that matches the configured wake phrase.
  // Multi-word phrases allow flexible whitespace, short words allow letter
  // spacing, and a leading "hey" is optional if the phrase starts with it.
  const words = phrase.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return /\bhey\s+sc\b/i;
  const lastPattern = wordPattern(words[words.length - 1]);
  const prefixWords = words.slice(0, -1);
  const prefixPattern = prefixWords.map(wordPattern).join('\\s+');
  const fullPattern = prefixPattern
    ? `${prefixPattern}\\s+${lastPattern}`
    : lastPattern;
  const optionalPrefix =
    prefixWords.length > 0 && prefixWords[0] === 'hey'
      ? `(?:${prefixPattern}\\s+)?${lastPattern}`
      : fullPattern;
  return new RegExp(`\\b${optionalPrefix}\\b`, 'i');
}

function playCue(frequency: number) {
  try {
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.08, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.12);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.12);
    oscillator.onended = () => { void context.close(); };
  } catch {}
}

export function useWakeWord() {
  const settings = useAppStore((state) => state.settings);
  const setWakeStatus = useAppStore((state) => state.setWakeStatus);
  const queueVoiceCommand = useAppStore((state) => state.queueVoiceCommand);
  const voicePlaybackState = useAppStore((state) => state.voicePlaybackState);
  const [backendSupported, setBackendSupported] = useState<boolean | null>(null);

  useEffect(() => {
    if (!settings.wakeOnboarded || !settings.wakeEnabled) {
      setWakeStatus(null);
      return;
    }

    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;

    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(wakeEventsUrl());
      socket.onopen = () => {
        reconnectAttempt = 0;
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as WakeEvent;
          // eslint-disable-next-line no-console
          console.log('[useWakeWord] event', event.type, event.transcript, event.state);
          if (event.type === 'wake_detected') playCue(660);
          if (event.type === 'command_ready' && event.transcript?.trim()) {
            playCue(880);
            // eslint-disable-next-line no-console
            console.log('[useWakeWord] command_ready -> queueVoiceCommand:', event.transcript.trim());
            queueVoiceCommand(event.transcript.trim());
            // Stop listening while the agent is generating / speaking so we
            // don't capture and re-trigger on the response audio or room echo.
            void setWakeListening(false).then(setWakeStatus).catch(() => {});
          }
          if (event.state) {
            const store = useAppStore.getState();
            const current = store.wakeStatus;
            if (current?.state === 'follow_up' && event.state === 'armed') store.setWakeConversationActive(false);
            if (current) setWakeStatus({ ...current, state: event.state, error: event.error ?? null });
          }
        } catch {}
      };
      socket.onclose = () => {
        if (disposed) return;
        const delay = Math.min(1000 * 2 ** reconnectAttempt, 30000);
        reconnectAttempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    fetchWakeStatus()
      .then(async (status) => {
        if (disposed) return;
        setBackendSupported(status.supported);
        setWakeStatus(status);
        if (status.supported) {
          if (!status.enabled) setWakeStatus(await setWakeListening(true));
          connect();
        }
      })
      .catch(() => {
        setBackendSupported(false);
      });

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [settings.wakeEnabled, settings.wakeOnboarded, queueVoiceCommand, setWakeStatus, setWakeListening]);

  // Mute the wake-word listener while the system is speaking so the TTS output
  // isn't re-interpreted as a new wake word / command.
  useEffect(() => {
    if (!settings.wakeOnboarded || !settings.wakeEnabled || !backendSupported) {
      return;
    }
    if (voicePlaybackState === 'speaking' || voicePlaybackState === 'loading') {
      void setWakeListening(false).then(setWakeStatus).catch(() => {});
    } else if (voicePlaybackState === 'idle') {
      const timer = setTimeout(() => {
        void setWakeListening(true).then(setWakeStatus).catch(() => {});
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [
    voicePlaybackState,
    settings.wakeEnabled,
    settings.wakeOnboarded,
    backendSupported,
    setWakeListening,
    setWakeStatus,
  ]);

  useEffect(() => {
    if (!settings.wakeOnboarded || !settings.wakeEnabled || !settings.wakeCloudFallback || backendSupported !== false) return;
    const recognitionWindow = window as SpeechRecognitionWindow;
    const Recognition = recognitionWindow.SpeechRecognition ?? recognitionWindow.webkitSpeechRecognition;
    if (!Recognition) return;

    const recognition = new Recognition();
    let disposed = false;
    let armedForCommand = false;
    let restartTimer: ReturnType<typeof setTimeout> | null = null;
    let commandTimer: ReturnType<typeof setTimeout> | null = null;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onresult = (event) => {
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = result[0]?.transcript?.trim() ?? '';
        const isFinal = result.isFinal;
        // eslint-disable-next-line no-console
        console.log('[useWakeWord] recognition result:', { transcript, isFinal, armedForCommand });
        const wakeMatch = buildWakePattern(useAppStore.getState().wakeStatus?.phrase || 'hey sc').exec(transcript);
        if (wakeMatch) {
          if (!armedForCommand) {
            playCue(660);
            armedForCommand = true;
          }
          if (commandTimer) clearTimeout(commandTimer);
          commandTimer = setTimeout(() => {
            armedForCommand = false;
            setWakeStatus({ state: 'armed', supported: true, enabled: true, model_installed: false, detector_mode: 'browser' });
          }, 15000);
          const command = transcript.slice((wakeMatch.index ?? 0) + wakeMatch[0].length).trim().replace(/^[,.:;!?-]+\s*/, '');
          if (isFinal) {
            setWakeStatus({ state: command ? 'command_ready' : 'capturing', supported: true, enabled: true, model_installed: false, detector_mode: 'browser' });
            if (command) {
              armedForCommand = false;
              if (commandTimer) clearTimeout(commandTimer);
              playCue(880);
              queueVoiceCommand(command);
            }
          }
        } else if (isFinal && armedForCommand && transcript) {
          armedForCommand = false;
          if (commandTimer) clearTimeout(commandTimer);
          setWakeStatus({ state: 'command_ready', supported: true, enabled: true, model_installed: false, detector_mode: 'browser' });
          playCue(880);
          queueVoiceCommand(transcript);
        }
      }
    };
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed') {
        disposed = true;
        setWakeStatus({ state: 'error', supported: true, enabled: false, model_installed: false, detector_mode: 'browser', error: 'Microphone permission denied' });
      }
    };
    recognition.onend = () => {
      if (!disposed) restartTimer = setTimeout(() => recognition.start(), 1000);
    };

    try {
      recognition.start();
      setWakeStatus({ state: 'armed', supported: true, enabled: true, model_installed: false, detector_mode: 'browser' });
    } catch {
      setWakeStatus({ state: 'error', supported: false, enabled: false, model_installed: false, detector_mode: 'browser', error: 'Browser wake recognition could not start' });
    }

    return () => {
      disposed = true;
      if (restartTimer) clearTimeout(restartTimer);
      if (commandTimer) clearTimeout(commandTimer);
      recognition.stop();
    };
  }, [backendSupported, settings.wakeCloudFallback, settings.wakeEnabled, settings.wakeOnboarded, queueVoiceCommand, setWakeStatus]);
}
