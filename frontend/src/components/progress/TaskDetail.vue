<script setup lang="ts">
// FH.1 详情 + FH.3 终态取结果。
// - 详情：双编号、名称、概括、中文状态、阶段、进度、预计时长、更新时间。
// - 取结果：终态后经 useTasksStore().loadResult → getTaskResult（§3.9），结果写入
//   tasks.latestResult，供阶段 E 结果标签消费；结果六块（含「任务状态」）陈列于此，
//   能对应到已产出产物的块，点按即打开该产物的中栏标签（动态标签模型，§3.12）；
//   无对应产物的块（如任务状态）只展示、不导航。
import { computed, ref } from 'vue'
import { NButton, NTag } from 'naive-ui'
import type { ArtifactRef, TaskView } from '@/api/types'
import { useArtifactsStore } from '@/stores/artifacts'
import { useTasksStore } from '@/stores/tasks'
import { useResultTabNav } from '@/composables/useResultTabNav'
import {
  timeBoundaryRows,
  useResultBlocks,
  versionRows,
} from '@/composables/useResultBlocks'
import {
  artifactForBlock,
  presentResultBlocks,
  type ResultBlockMeta,
} from '@/components/chat/resultBlocks'
import MlResultView from '@/components/results/ml/MlResultView.vue'
import { formatTimestamp } from '@/components/resources/resourceUtils'
import {
  abortedHint,
  activeHint,
  clampPercent,
  formatDuration,
  hasEstimate,
  isTerminal,
  statusLabel,
  statusTagType,
} from './taskDisplay'

const props = defineProps<{
  task: TaskView
  conversationId: string | null
}>()

const tasks = useTasksStore()
const artifacts = useArtifactsStore()
const nav = useResultTabNav()

const terminal = computed(() => isTerminal(props.task.status))
const label = computed(() => statusLabel(props.task))
const tagType = computed(() => statusTagType(props.task.status))
const percent = computed(() => clampPercent(props.task.progress_percent))
const showEstimate = computed(() => hasEstimate(props.task))
const estimate = computed(() => formatDuration(props.task.estimated_seconds))
const runningHint = computed(() => activeHint(props.task.status))
const stoppedHint = computed(() => abortedHint(props.task.status))

const result = computed(() => tasks.getResult(props.task.agent_task_id))
const loaded = computed(() => result.value !== null)
const blocks = computed(() => presentResultBlocks(result.value?.blocks))

// 版本与时间边界（FE-5）：直接展示本次任务结果板块里的原值，不推断。
const { versions, timeBoundaries } = useResultBlocks(result)

/** 每个结果块在该对话产物里能打开的产物；无则 null（只展示、不导航）。 */
const blockArtifacts = computed<Record<string, ArtifactRef | null>>(() => {
  const list = artifacts.list(props.conversationId)
  const map: Record<string, ArtifactRef | null> = {}
  for (const block of blocks.value) {
    map[block.key] = artifactForBlock(block, list)
  }
  return map
})

const loading = ref(false)

/** 点结果块：打开该块对应产物的标签（动态标签模型），无对应产物则不导航。 */
function openArtifact(block: ResultBlockMeta): void {
  const artifact = blockArtifacts.value[block.key]
  if (!artifact || !nav) return
  if (props.conversationId) artifacts.openTab(props.conversationId, artifact.id)
  nav(artifact.id)
}

async function loadResult(): Promise<void> {
  if (!props.conversationId) return
  loading.value = true
  try {
    await tasks.loadResult(props.conversationId, props.task.agent_task_id)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="task-detail" data-testid="task-detail">
    <div class="task-detail__head">
      <span class="task-detail__name" data-testid="detail-name">
        {{ task.name || task.agent_task_id }}
      </span>
      <n-tag
        size="small"
        :type="tagType"
        :bordered="false"
        data-testid="detail-status"
      >
        {{ label }}
      </n-tag>
    </div>

    <dl class="task-detail__fields">
      <div>
        <dt>Agent 编号</dt>
        <dd data-testid="detail-agent-id">{{ task.agent_task_id }}</dd>
      </div>
      <div>
        <dt>Hadoop 编号</dt>
        <dd data-testid="detail-hadoop-id">{{ task.hadoop_task_id || '—' }}</dd>
      </div>
      <div>
        <dt>概括</dt>
        <dd data-testid="detail-summary">{{ task.summary || '—' }}</dd>
      </div>
      <div>
        <dt>阶段</dt>
        <dd data-testid="detail-stage">{{ task.stage || '—' }}</dd>
      </div>
      <div>
        <dt>进度</dt>
        <dd data-testid="detail-percent">{{ percent }}%</dd>
      </div>
      <div v-if="showEstimate">
        <dt>预计时长</dt>
        <dd data-testid="detail-estimate">约 {{ estimate }}</dd>
      </div>
      <div>
        <dt>更新时间</dt>
        <dd data-testid="detail-updated">{{ formatTimestamp(task.updated_at) }}</dd>
      </div>
    </dl>

    <p
      v-if="runningHint"
      class="task-detail__progress-hint"
      data-testid="detail-running-hint"
    >
      {{ runningHint }}
    </p>
    <p
      v-else-if="stoppedHint"
      class="task-detail__progress-hint task-detail__progress-hint--stopped"
      data-testid="detail-aborted-note"
    >
      {{ stoppedHint }}
    </p>

    <div class="task-detail__result">
      <template v-if="terminal">
        <n-button
          size="tiny"
          type="primary"
          :loading="loading"
          :disabled="!conversationId"
          data-testid="task-load-result"
          @click="loadResult"
        >
          取结果
        </n-button>

        <div v-if="loaded" class="task-detail__blocks" data-testid="task-result-blocks">
          <span class="task-detail__hint">结果已载入：</span>
          <template v-for="block in blocks" :key="block.key">
            <n-button
              v-if="blockArtifacts[block.key] && nav"
              size="tiny"
              secondary
              :data-testid="`task-result-link-${block.key}`"
              @click="openArtifact(block)"
            >
              {{ block.label }}
            </n-button>
            <n-tag
              v-else
              size="small"
              :bordered="false"
              :data-testid="`task-result-link-${block.key}`"
            >
              {{ block.label }}
            </n-tag>
          </template>
          <span v-if="!blocks.length" class="task-detail__hint">
            该结果无可展示的数据块。
          </span>
        </div>

        <div
          v-if="loaded && (versions || timeBoundaries)"
          class="task-detail__versions"
          data-testid="task-result-versions"
        >
          <template v-if="versions">
            <span class="task-detail__versions-title">版本</span>
            <ul class="task-detail__versions-list">
              <li
                v-for="row in versionRows(versions)"
                :key="`version-${row.key}`"
                :data-testid="`task-version-${row.key}`"
              >
                {{ row.label }}：{{ row.value }}
              </li>
            </ul>
          </template>
          <template v-if="timeBoundaries">
            <span class="task-detail__versions-title">时间边界（训练期/验证期）</span>
            <ul class="task-detail__versions-list">
              <li
                v-for="row in timeBoundaryRows(timeBoundaries)"
                :key="`boundary-${row.key}`"
                :data-testid="`task-time-boundary-${row.key}`"
              >
                {{ row.label }}：{{ row.value }}
              </li>
            </ul>
          </template>
        </div>

        <MlResultView v-if="loaded" class="task-detail__ml" :result="result" />
      </template>

      <p v-else class="task-detail__hint" data-testid="task-result-pending">
        任务未结束，暂无结果。
      </p>
    </div>
  </section>
</template>

<style scoped>
.task-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 4px;
  padding: 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
  background: var(--n-color-modal, #fafafc);
}
.task-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.task-detail__name {
  font-size: var(--fs-md);
  font-weight: 600;
}
.task-detail__fields {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
}
.task-detail__fields div {
  display: flex;
  gap: 8px;
  font-size: var(--fs-md);
}
.task-detail__fields dt {
  flex: 0 0 auto;
  width: 68px;
  color: var(--n-text-color-3, #999);
}
.task-detail__fields dd {
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  text-overflow: ellipsis;
  word-break: break-all;
}
.task-detail__progress-hint {
  margin: 0;
  color: var(--n-text-color-2, #666);
  font-size: var(--fs-sm);
}
.task-detail__progress-hint--stopped {
  color: var(--n-warning-color, #f0a020);
}
.task-detail__result {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
}
.task-detail__blocks {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.task-detail__hint {
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.task-detail__versions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 2px;
}
.task-detail__versions-title {
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
  font-weight: 600;
}
.task-detail__versions-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.task-detail__versions-list li {
  color: var(--n-text-color-2, #666);
  font-size: var(--fs-sm);
  word-break: break-all;
}
.task-detail__ml {
  width: 100%;
  margin-top: 2px;
}
</style>
