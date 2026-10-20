<script setup lang="ts">
// 方案依据（真实数据）：展示结果里的四类版本与 T1/T2、局限说明；
// 方案正文经「方案内容」中栏标签查看（复用左栏资源页同一套读取）。
import { computed } from 'vue'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { schemeTab } from '@/components/centerTabViews'
import {
  useResultBlocks,
  timeBoundaryRows,
  versionRows,
} from '@/composables/useResultBlocks'

const { result, versions, timeBoundaries, limitations } = useResultBlocks()
const centerTabs = useCenterTabsStore()

const verRows = computed(() => versionRows(versions.value))
const tbRows = computed(() => timeBoundaryRows(timeBoundaries.value))
const hasBoundaries = computed(() => verRows.value.length > 0 || tbRows.value.length > 0)

const CLEANING = 'cleaning_rules.v1.json'
const SCORING = 'scoring_scheme.v1.json'

function openScheme(name: string, label: string): void {
  centerTabs.open(schemeTab(name, label))
}
</script>

<template>
  <section class="view active" data-view="basis" data-testid="view-basis">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>方案依据</h2>
          <div class="panel-sub">版本 · 时间边界 · 局限（均取自最近一次结果）</div>
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
          <div class="actions">
            <button
              class="btn sm"
              data-testid="basis-open-cleaning"
              @click="openScheme(CLEANING, '清洗方案')"
            >
              查看清洗方案
            </button>
            <button
              class="btn sm"
              data-testid="basis-open-scoring"
              @click="openScheme(SCORING, '评分方案')"
            >
              查看评分方案
            </button>
          </div>
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
