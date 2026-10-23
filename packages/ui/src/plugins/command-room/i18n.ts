/**
 * Command-room scoped i18n — plugin bundles shipped under the plugin id (the
 * kanban pattern): registered via `ctx.i18n.register` at load, read through
 * `usePluginI18n('command-room')`, never touching core en.ts. The vote copy is
 * ported verbatim from the fork's hermes-bots i18n (en, ja, zh-Hans, zh-Hant);
 * the protocol words themselves (`decide:` / `vote:` / `decided:`) stay
 * untranslated in the room log.
 */

import { type PluginLocaleBundles, type PluginTranslate, usePluginI18n } from '@hermes/plugin-sdk'
import { useMemo } from 'react'

type ConsensusWord = 'majority' | 'split' | 'unanimous' | 'waiting'

type VoteMessages = {
  label: (question: string) => string
  title: string
  option: string
  picked: (names: string) => string
  missing: (names: string) => string
  decided: (option: string) => string
  callIt: string
  callHint: string
  consensus: Record<ConsensusWord, string>
}

export type CommandRoomMessages = { vote: VoteMessages }

const en: CommandRoomMessages = {
  vote: {
    label: question => `Team vote: ${question}`,
    title: 'Team vote',
    option: 'Option',
    picked: names => `${names} picked this`,
    missing: names => `waiting on ${names}`,
    decided: option => `Decided: ${option}`,
    callIt: 'Call it',
    callHint: 'your call — the card records it in the room',
    consensus: { majority: 'Majority', split: 'Split', unanimous: 'Unanimous', waiting: 'Waiting' }
  }
}

const ja: CommandRoomMessages = {
  vote: {
    label: question => `チーム投票: ${question}`,
    title: 'チーム投票',
    option: '選択肢',
    picked: names => `${names} が選択`,
    missing: names => `${names} が未投票`,
    decided: option => `決定: ${option}`,
    callIt: '決定する',
    callHint: 'あなたの決定 — カードが部屋に記録します',
    consensus: { majority: '過半数', split: '割れた', unanimous: '全員一致', waiting: '投票待ち' }
  }
}

const zh: CommandRoomMessages = {
  vote: {
    label: question => `团队投票：${question}`,
    title: '团队投票',
    option: '选项',
    picked: names => `${names} 选择了这个`,
    missing: names => `${names} 还没投`,
    decided: option => `已决定：${option}`,
    callIt: '拍板',
    callHint: '由你拍板 — 卡片会记录到房间里',
    consensus: { majority: '多数', split: '分歧', unanimous: '全票', waiting: '等待投票' }
  }
}

const zhHant: CommandRoomMessages = {
  vote: {
    label: question => `團隊投票：${question}`,
    title: '團隊投票',
    option: '選項',
    picked: names => `${names} 選了這個`,
    missing: names => `${names} 還沒投`,
    decided: option => `已決定：${option}`,
    callIt: '拍板',
    callHint: '由你拍板 — 卡片會記錄到房間裡',
    consensus: { majority: '多數', split: '分歧', unanimous: '全票', waiting: '等待投票' }
  }
}

/** Registered via `ctx.i18n.register` at plugin load (disposer tracked). */
export const COMMAND_ROOM_LOCALES: PluginLocaleBundles = { en, ja, zh, 'zh-hant': zhHant }

// Bind the message SHAPE to a plugin translator: string leaves resolve now,
// function leaves forward their args through t(path, …). One tiny generic
// instead of a hand-written accessor per key (the kanban i18n pattern).
type Bound<T> = {
  [K in keyof T]: T[K] extends (...args: infer A) => string
    ? (...args: A) => string
    : T[K] extends object
      ? Bound<T[K]>
      : string
}

function bind<T extends object>(t: PluginTranslate, template: T, prefix = ''): Bound<T> {
  const out = {} as Record<string, unknown>

  for (const [key, value] of Object.entries(template)) {
    const path = prefix ? `${prefix}.${key}` : key
    out[key] =
      typeof value === 'function'
        ? (...args: unknown[]) => t(path, ...args)
        : value && typeof value === 'object'
          ? bind(t, value as object, path)
          : t(path)
  }

  return out as Bound<T>
}

export type CommandRoomText = Bound<CommandRoomMessages>

/** The command-room strings for the active locale — one hook the card reads. */
export function useVoteCopy(): CommandRoomText {
  const t = usePluginI18n('command-room')

  return useMemo(() => bind(t, en), [t])
}
