// 模型产物的展示辅助：按 `role` 分组、role→中文名、文本类 kind 判定。
// 供模型详情（中栏资源标签 ModelDetailView）与左栏「模型」段共用，避免重复实现分组逻辑。
// 分组顺序与中文名以契约 §4.2 的建议为准；未知 role 归「其它」。

import type { MlArtifact, MlArtifactKind, MlArtifactRole } from '@/api/models'

/** 已知 role 的固定展示顺序。 */
export const MODEL_ROLE_ORDER: readonly MlArtifactRole[] = [
  'model',
  'profile',
  'labels',
  'coords',
  'meta',
]

/** role → 中文名；未知 role 回落「其它」。 */
export const MODEL_ROLE_LABEL: Record<string, string> = {
  model: '模型本体',
  profile: '群体画像',
  labels: '每用户标签',
  coords: '用户坐标',
  meta: '模型元数据',
}

/** 可在线预览的文本类 kind。 */
export const MODEL_TEXT_KINDS: readonly MlArtifactKind[] = ['json', 'csv', 'jsonl', 'md']

/** 某 kind 是否可在线预览（文本类）。 */
export function isTextArtifactKind(kind: MlArtifactKind): boolean {
  return (MODEL_TEXT_KINDS as string[]).includes(kind)
}

/** 按 role 分组后的一个分组。 */
export interface ModelArtifactGroup {
  role: string
  label: string
  items: MlArtifact[]
}

/** 把产物按 role 分组：已知 role 按其固定顺序在前，其余（含缺席 role）追加在后。 */
export function groupArtifactsByRole(
  artifacts: readonly MlArtifact[] | null | undefined,
): ModelArtifactGroup[] {
  const byRole = new Map<string, MlArtifact[]>()
  for (const artifact of artifacts ?? []) {
    const role = artifact.role || 'other'
    const bucket = byRole.get(role)
    if (bucket) bucket.push(artifact)
    else byRole.set(role, [artifact])
  }
  const ordered: string[] = []
  for (const role of MODEL_ROLE_ORDER) if (byRole.has(role)) ordered.push(role)
  for (const role of byRole.keys()) if (!MODEL_ROLE_ORDER.includes(role)) ordered.push(role)
  return ordered.map((role) => ({
    role,
    label: MODEL_ROLE_LABEL[role] ?? '其它',
    items: byRole.get(role) ?? [],
  }))
}
