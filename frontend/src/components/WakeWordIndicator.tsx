import { Mic, MicOff } from 'lucide-react';
import { useAppStore } from '../lib/store';

const LABELS = {
  disabled: 'Wake word off',
  starting: 'Starting listener',
  armed: 'Hey Jarvis armed',
  capturing: 'Listening',
  transcribing: 'Transcribing',
  command_ready: 'Command ready',
  follow_up: 'Follow-up listening',
  error: 'Wake word error',
} as const;

export function WakeWordIndicator() {
  const enabled = useAppStore((state) => state.settings.wakeEnabled);
  const status = useAppStore((state) => state.wakeStatus);
  if (!enabled) return null;
  const state = status?.state ?? 'starting';
  const Icon = state === 'disabled' || state === 'error' ? MicOff : Mic;
  return (
    <div
      className="fixed bottom-3 right-3 z-40 flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] shadow-sm"
      style={{ backgroundColor: 'var(--color-surface)', border: '1px solid var(--color-border)', color: state === 'error' ? 'var(--color-error)' : 'var(--color-text-secondary)' }}
      title={status?.error || LABELS[state]}
    >
      <Icon size={12} />
      {LABELS[state]}
    </div>
  );
}
