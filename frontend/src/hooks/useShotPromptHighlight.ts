import { useMemo } from "react";
import type { MentionKind } from "@/components/canvas/reference/asset-colors";
import { MENTION_RE, mentionNameFromMatch } from "@/utils/reference-mentions";

/**
 * Shot/@mention/seedance tokenizer for the reference-video prompt editor.
 *
 * Recognised token kinds:
 * - shot_header:    "Shot N (Xs):" prefix (standard & seedance)
 * - mention:        @角色名 / @[道具名] asset references
 * - seedance_position:   【站位】：... line
 * - seedance_forbidden:  【禁止标签】：... line
 * - seedance_timeblock:  {0-X秒 | 镜头：...} time blocks
 * - seedance_meta:       无水印无字幕 / 结尾保持静止不漂移
 * - text:           everything else
 */

export type MentionLookup = Record<string, "character" | "scene" | "prop">;

export type Token =
  | { kind: "text"; text: string }
  | { kind: "shot_header"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind }
  | { kind: "seedance_position"; text: string }
  | { kind: "seedance_forbidden"; text: string }
  | { kind: "seedance_timeblock"; text: string }
  | { kind: "seedance_meta"; text: string };

const SHOT_HEADER_RE = /^Shot\s+\d+\s*\(\s*\d+\s*s\s*\)\s*:\s*/i;
const POSITION_RE = /^【站位】[：:]/;
const FORBIDDEN_RE = /^【禁止标签】[：:]/;
const TIMEBLOCK_RE = /^\{(\d+-\d+秒)\s*\|\s*/;
const META_RE = /^无水印无字幕[，,]\s*/;

export function tokenizePrompt(text: string, lookup: MentionLookup): Token[] {
  if (text.length === 0) return [];
  const tokens: Token[] = [];
  const lines = text.split(/(\n)/);

  for (const piece of lines) {
    if (piece === "\n") {
      tokens.push({ kind: "text", text: "\n" });
      continue;
    }

    // Seedance markers → colour the marker prefix, then parse rest for @mentions
    if (FORBIDDEN_RE.test(piece)) {
      const m = piece.match(FORBIDDEN_RE)!;
      tokens.push({ kind: "seedance_forbidden", text: m[0] });
      if (piece.length > m[0].length) pushMentionTokens(tokens, piece.slice(m[0].length), lookup);
      continue;
    }
    if (POSITION_RE.test(piece)) {
      const m = piece.match(POSITION_RE)!;
      tokens.push({ kind: "seedance_position", text: m[0] });
      if (piece.length > m[0].length) pushMentionTokens(tokens, piece.slice(m[0].length), lookup);
      continue;
    }

    // Time block
    const tbMatch = piece.match(TIMEBLOCK_RE);
    if (tbMatch) {
      // leading meta prefix: "无水印无字幕, "
      const metaMatch = piece.match(META_RE);
      const afterMeta = metaMatch ? piece.slice(metaMatch[0].length) : piece;
      if (metaMatch) {
        tokens.push({ kind: "seedance_meta", text: metaMatch[0] });
      }
      // Time block prefix: {0-X秒 |  — 后续内容可含 @角色，走 mention 解析
      const tbHdr = afterMeta.match(/^\{(\d+-\d+秒)\s*\|\s*/);
      if (tbHdr) {
        tokens.push({ kind: "seedance_timeblock", text: tbHdr[0] });
        const rest = afterMeta.slice(tbHdr[0].length);
        pushMentionTokens(tokens, rest, lookup);
      } else {
        tokens.push({ kind: "seedance_timeblock", text: afterMeta });
      }
      continue;
    }

    // Meta prefix at line start
    const metaPrefix = piece.match(META_RE);
    if (metaPrefix) {
      tokens.push({ kind: "seedance_meta", text: metaPrefix[0] });
      const rest = piece.slice(metaPrefix[0].length);
      const sm = rest.match(SHOT_HEADER_RE);
      if (sm) {
        tokens.push({ kind: "shot_header", text: sm[0] });
        if (rest.length > sm[0].length) pushMentionTokens(tokens, rest.slice(sm[0].length), lookup);
      } else {
        pushMentionTokens(tokens, rest, lookup);
      }
      continue;
    }

    // Fall through: standard shot header / mention tokenisation
    const shotMatch = piece.match(SHOT_HEADER_RE);
    if (shotMatch) {
      tokens.push({ kind: "shot_header", text: shotMatch[0] });
      const rest = piece.slice(shotMatch[0].length);
      if (rest.length > 0) pushMentionTokens(tokens, rest, lookup);
    } else {
      pushMentionTokens(tokens, piece, lookup);
    }
  }

  return tokens;
}

function pushMentionTokens(out: Token[], text: string, lookup: MentionLookup): void {
  let lastIdx = 0;
  for (const m of text.matchAll(MENTION_RE)) {
    const idx = m.index ?? 0;
    if (idx > lastIdx) {
      out.push({ kind: "text", text: text.slice(lastIdx, idx) });
    }
    const name = mentionNameFromMatch(m);
    const resolved = lookup[name];
    out.push({
      kind: "mention",
      text: m[0],
      name,
      assetKind: (resolved ?? "unknown") as MentionKind,
    });
    lastIdx = idx + m[0].length;
  }
  if (lastIdx < text.length) {
    out.push({ kind: "text", text: text.slice(lastIdx) });
  }
}

/**
 * React hook wrapper around tokenizePrompt. Memoizes by (text, lookup identity).
 * Callers should `useMemo` the lookup object to keep the reference stable.
 */
export function useShotPromptHighlight(text: string, lookup: MentionLookup): Token[] {
  return useMemo(() => tokenizePrompt(text, lookup), [text, lookup]);
}
