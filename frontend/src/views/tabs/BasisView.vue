<script setup lang="ts">
// 方案依据（真实数据）：展示当前任务结果的四类版本与 T1/T2、局限说明；
// 方案正文从产物 store 里按 source=scheme 派生（用 scheme_name 打开），不写死文件名。
import { computed } from 'vue'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import { schemeTab } from '@/components/centerTabViews'
import {
  useResultBlocks,
  timeBoundaryRows,
  versionRows,
} from '@/composables/useResultBlocks'
import { useTasksStore } from '@/stores/tasks'
import type { ArtifactRef } from '@/api/types'

const tasks = useTasksStore()
const { result, versions, timeBoundaries, limitations } = useResultBlocks(
  () => tasks.currentResult,
)
const artifacts = useArtifactsStore()
const conversations = useConversationsStore()
const centerTabs = useCenterTabsStore()

const verRows = computed(() => versionRows(versions.value))
const tbRows = computed(() => timeBoundaryRows(timeBoundaries.value))
const hasBoundaries = computed(() => verRows.value.length > 0 || tbRows.value.length > 0)

const schemeArtifacts = computed<ArtifactRef[]>(() => {
  const cid = conversations.currentId
  if (!cid) return []
  return artifacts
    .list(cid)
    .filter((item) => item.source === 'scheme' && Boolean(item.scheme_name))
})

function schemeLabel(artifact: ArtifactRef): string {
  return artifact.title || artifact.scheme_name || artifact.id
}

function openScheme(artifact: ArtifactRef): void {
  const name = artifact.scheme_name
  if (!name) return
  centerTabs.open(schemeTab(name, schemeLabel(artifact)))
}
</script>

<template>
  <section class="view active" data-view="basis" data-testid="view-basis">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>方案依据</h2>
          <div class="panel-sub">版本 · 时间边界 · 局限（均取自当前任务结果）</div>
        </div>
      </div>

      <p v-if="!result" class="panel-sub" data-testid="basis-empty">
        暂无结果。先在对话里发起一次清洗 / 评分任务。
      </p>

      <template v-else>
        <div class="card">
          <h3>版本与时间边界</h3>
          <table v-if="hasBoundaries" class="data">
            <tbody>
              <tr v-for="row in verRows" :key="row.key">
                <td>{{ row.label }}</td>
                <td>{{ row.value || '—' }}</td>
              </tr>
              <tr v-for="row in tbRows" :key="row.key">
                <td>{{ row.label }}</td>
                <td>{{ row.value || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="panel-sub" data-testid="basis-no-boundaries">该结果没有版本或时间边界信息。</p>
        </div>

        <div class="card">
          <h3>方案正文</h3>
          <div v-if="schemeArtifacts.length" class="actions">
            <button
              v-for="artifact in schemeArtifacts"
              :key="artifact.id"
              class="btn sm"
              :data-testid="`basis-scheme-${artifact.id}`"
              @click="openScheme(artifact)"
            >
              查看 {{ schemeLabel(artifact) }}
            </button>
          </div>
          <p v-else class="panel-sub" data-testid="basis-no-schemes">该结果未提供方案文件</p>
        </div>

        <div class="card">
          <h3>局限与依据</h3>
          <ul v-if="limitations.length" class="basis-list" data-testid="basis-limitations">
            <li v-for="(item, index) in limitations" :key="index">{{ item }}</li>
          </ul>
          <p v-else class="panel-sub" data-testid="basis-no-limitations">该结果未提供局限说明。</p>
        </div>
      </template>
    </div>
  </section>
</template>
