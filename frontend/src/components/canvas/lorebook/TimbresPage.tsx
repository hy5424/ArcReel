import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Music } from "lucide-react";
import { GalleryToolbar } from "./GalleryToolbar";
import { GalleryEmptyState } from "./GalleryEmptyState";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";

interface TimbreEntry {
  description?: string;
  audio_file?: string;
  gender?: string;
  age_range?: string;
}

interface Props {
  projectName: string;
  timbres: Record<string, TimbreEntry>;
  onUpdateTimbre: (name: string, updates: Partial<TimbreEntry>) => void;
  onAddTimbre: (name: string, description: string, audioFile?: File) => Promise<void>;
  onRefreshProject?: () => Promise<void> | void;
}

export function TimbresPage({ projectName, timbres, onUpdateTimbre, onAddTimbre, onRefreshProject }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formGender, setFormGender] = useState("");
  const [formAge, setFormAge] = useState("");
  const [formAudio, setFormAudio] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const entries = Object.entries(timbres);

  const handleAdd = async () => {
    if (!formName.trim()) return;
    setSubmitting(true);
    try {
      await onAddTimbre(formName.trim(), formDesc.trim(), formAudio ?? undefined);
      setAdding(false);
      setFormName(""); setFormDesc(""); setFormGender(""); setFormAge(""); setFormAudio(null);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <GalleryToolbar title={t("dashboard:timbres")} count={entries.length} onAdd={() => setAdding(true)} onPickFromLibrary={() => {}} />
      <div className="px-5 py-5">
        {entries.length === 0 ? (
          <GalleryEmptyState icon={<Music className="h-6 w-6" />} label={t("dashboard:timbres")} hint="上传音色参考音频" onClick={() => setAdding(true)} />
        ) : (
          <div className="grid justify-evenly gap-4 [grid-template-columns:repeat(auto-fill,320px)]">
            {entries.map(([name, info]) => (
              <div key={name} className="rounded-xl p-4 border" style={{ background: "oklch(0.18 0.012 270 / 0.5)", borderColor: "var(--color-hairline)" }}>
                <div className="flex items-center gap-2 mb-2">
                  <Music className="h-4 w-4" style={{ color: "var(--color-accent-2)" }} />
                  <span className="text-[14px] font-medium" style={{ color: "var(--color-text)" }}>{name}</span>
                </div>
                {info.gender && <div className="text-[11px]" style={{ color: "var(--color-text-3)" }}>{info.gender}{info.age_range ? ` · ${info.age_range}` : ""}</div>}
                {info.description && <div className="mt-1 text-[12px]" style={{ color: "var(--color-text-4)" }}>{info.description}</div>}
                {info.audio_file && <div className="mt-2 text-[10px] font-mono" style={{ color: "var(--color-accent-2)" }}>{info.audio_file}</div>}
              </div>
            ))}
          </div>
        )}
      </div>

      {adding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setAdding(false)}>
          <div className="w-[480px] rounded-2xl p-6" style={{ background: "oklch(0.16 0.010 265 / 0.95)", border: "1px solid var(--color-hairline)" }} onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-semibold mb-4" style={{ color: "var(--color-text)" }}>上传音色</h3>
            <div className="flex flex-col gap-3">
              <input value={formName} onChange={e => setFormName(e.target.value)} placeholder="音色名称 *" className="rounded-lg px-3 py-2 text-[13px] outline-none" style={{ background: "oklch(0.22 0.010 265 / 0.6)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }} />
              <input value={formDesc} onChange={e => setFormDesc(e.target.value)} placeholder="描述（如：低沉温暖的男声）" className="rounded-lg px-3 py-2 text-[13px] outline-none" style={{ background: "oklch(0.22 0.010 265 / 0.6)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }} />
              <select value={formGender} onChange={e => setFormGender(e.target.value)} className="rounded-lg px-3 py-2 text-[13px] outline-none" style={{ background: "oklch(0.22 0.010 265 / 0.6)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }}>
                <option value="">性别</option>
                <option value="male">男</option>
                <option value="female">女</option>
                <option value="neutral">中性</option>
              </select>
              <input value={formAge} onChange={e => setFormAge(e.target.value)} placeholder="年龄段（如：青年/中年）" className="rounded-lg px-3 py-2 text-[13px] outline-none" style={{ background: "oklch(0.22 0.010 265 / 0.6)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }} />
              <input type="file" accept=".wav,.mp3" onChange={e => setFormAudio(e.target.files?.[0] ?? null)} className="text-[13px]" style={{ color: "var(--color-text-2)" }} />
              <div className="flex gap-2 justify-end mt-2">
                <button onClick={() => setAdding(false)} className="px-4 py-2 rounded-lg text-[13px]" style={{ background: "oklch(0.22 0.010 265 / 0.6)", color: "var(--color-text-3)" }}>取消</button>
                <button onClick={handleAdd} disabled={submitting || !formName.trim()} className="px-4 py-2 rounded-lg text-[13px] font-medium" style={{ background: "var(--color-accent-dim)", color: "var(--color-accent-2)", opacity: submitting || !formName.trim() ? 0.5 : 1 }}>上传</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
