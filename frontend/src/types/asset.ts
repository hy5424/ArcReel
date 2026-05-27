export type AssetType = "character" | "scene" | "prop" | "timbre";

export interface Asset {
  id: string;
  type: AssetType;
  name: string;
  description: string;
  voice_style: string;
  image_path: string | null;
  audio_file?: string | null;
  gender?: string;
  age_range?: string;
  source_project: string | null;
  updated_at: string | null;
}

export interface AssetCreatePayload {
  type: AssetType;
  name: string;
  description?: string;
  voice_style?: string;
  gender?: string;
  age_range?: string;
}

export interface AssetUpdatePayload {
  name?: string;
  description?: string;
  voice_style?: string;
  gender?: string;
  age_range?: string;
}
