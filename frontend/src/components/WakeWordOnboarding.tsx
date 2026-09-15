import { useState } from 'react';
import { Mic, ShieldCheck } from 'lucide-react';
import { installWakeModel, setWakeListening, updateWakeSettings } from '../lib/api';
import { useAppStore } from '../lib/store';

export function WakeWordOnboarding() {
  const settings = useAppStore((state) => state.settings);
  const updateSettings = useAppStore((state) => state.updateSettings);
  const setWakeStatus = useAppStore((state) => state.setWakeStatus);
  const wakeStatus = useAppStore((state) => state.wakeStatus);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState('');

  if (settings.wakeOnboarded) return null;

  const enable = async () => {
    setInstalling(true);
    setError('');
    try {
      const phrase = wakeStatus?.phrase || 'hey sc';
      const canLocal = phrase === 'hey jarvis' || wakeStatus?.model_installed;
      if (canLocal) {
        await installWakeModel(true);
        await updateWakeSettings({ enabled: true, auto_start: true });
        const status = await setWakeListening(true);
        updateSettings({ wakeOnboarded: true, wakeEnabled: true });
        setWakeStatus(status);
      } else {
        // No downloadable local model for this phrase; use browser speech
        // recognition fallback (cloud, vendor-dependent) for "Hey SC".
        await updateWakeSettings({ browser_fallback_consent: true });
        updateSettings({ wakeOnboarded: true, wakeEnabled: true, wakeCloudFallback: true });
        setWakeStatus({ state: 'armed', supported: true, enabled: true, model_installed: false, detector_mode: 'browser' });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Wake-word setup failed');
    } finally {
      setInstalling(false);
    }
  };

  const skip = () => updateSettings({ wakeOnboarded: true, wakeEnabled: false });

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}>
      <div className="w-full max-w-lg rounded-2xl p-6 shadow-2xl" style={{ backgroundColor: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl" style={{ backgroundColor: 'var(--color-accent-subtle)', color: 'var(--color-accent)' }}>
          <Mic size={24} />
        </div>
        <h2 className="text-xl font-semibold" style={{ color: 'var(--color-text)' }}>Enable “Hey Jarvis”</h2>
        <p className="mt-2 text-sm leading-6" style={{ color: 'var(--color-text-secondary)' }}>
          OpenJarvis can listen locally for “Hey Jarvis” to wake <strong>SC_Assist</strong>, capture the command that follows, and return to low-power wake listening after each request. Raw microphone audio is not retained.
        </p>
        <div className="mt-4 rounded-xl p-3 text-xs leading-5" style={{ backgroundColor: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)' }}>
          <div className="mb-1 flex items-center gap-2 font-medium" style={{ color: 'var(--color-text)' }}><ShieldCheck size={14} /> Local model license</div>
          The optional openWakeWord “Hey Jarvis” model is downloaded only with your consent and is licensed CC BY-NC-SA 4.0. Its non-commercial and share-alike restrictions apply to the model weights. A local “Hey SC” model is not yet available; it can be trained and swapped in once the weights are ready.
        </div>
        {error && <p className="mt-3 text-sm" style={{ color: 'var(--color-error)' }}>{error}</p>}
        <div className="mt-6 flex justify-end gap-2">
          <button onClick={skip} disabled={installing} className="rounded-lg px-4 py-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>Not now</button>
          <button onClick={() => { void enable(); }} disabled={installing} className="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50" style={{ backgroundColor: 'var(--color-accent)', color: 'var(--color-on-accent)' }}>
            {installing ? 'Enabling…' : 'Enable'}
          </button>
        </div>
      </div>
    </div>
  );
}
