import { Smartphone } from "lucide-react";
import { useEffect, useId, useState } from "react";

interface InstallPromptEvent extends Event {
  prompt(): Promise<{ outcome: "accepted" | "dismissed" }>;
}

function getMobilePlatform() {
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)) {
    return "ios";
  }
  return /Android/i.test(navigator.userAgent) ? "android" : null;
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

export function HomeScreenInstall() {
  const [platform] = useState(getMobilePlatform);
  const [standalone, setStandalone] = useState(isStandalone);
  const [installed, setInstalled] = useState(false);
  const [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(null);
  const [prompting, setPrompting] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);
  const instructionsId = useId();

  useEffect(() => {
    if (!platform) return;

    const displayMode = window.matchMedia("(display-mode: standalone)");
    const updateDisplayMode = () => setStandalone(isStandalone());
    const capturePrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as InstallPromptEvent);
    };
    const onInstalled = () => {
      setInstallPrompt(null);
      setInstalled(true);
    };

    displayMode.addEventListener("change", updateDisplayMode);
    window.addEventListener("beforeinstallprompt", capturePrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      displayMode.removeEventListener("change", updateDisplayMode);
      window.removeEventListener("beforeinstallprompt", capturePrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, [platform]);

  if (!platform || standalone || installed) return null;

  const handleInstall = async () => {
    if (!installPrompt) {
      setShowInstructions((open) => !open);
      return;
    }

    // The browser prompt is single-use and must be opened from a user gesture.
    setInstallPrompt(null);
    setShowInstructions(false);
    setPrompting(true);
    try {
      const { outcome } = await installPrompt.prompt();
      if (outcome === "accepted") setInstalled(true);
    } catch {
      setShowInstructions(true);
    } finally {
      setPrompting(false);
    }
  };

  return (
    <section className="home-screen-install" aria-label="Install LullabAI Twins">
      <button
        className="menu-button home-screen-button"
        type="button"
        onClick={handleInstall}
        disabled={prompting}
        aria-controls={installPrompt ? undefined : instructionsId}
        aria-expanded={installPrompt ? undefined : showInstructions}
      >
        <Smartphone aria-hidden="true" /> Add to Home Screen
      </button>
      <p className="section-caption" id={instructionsId} hidden={!showInstructions}>
        {platform === "ios"
          ? <>Open this page in Safari, tap <strong>Share</strong> → <strong>Add to Home Screen</strong> → <strong>Add</strong>.</>
          : <>Open your browser menu (⋮) and choose <strong>Add to Home screen</strong> or <strong>Install app</strong> if available.</>}
      </p>
    </section>
  );
}
