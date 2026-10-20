<script setup lang="ts">
// 阶段 I · FI.2 →（抽为可复用渲染件）：方案 JSON（评分方案 / 清洗规则方案）渲染。
// 评分方案渲染维度/指标/权重/适用范围/局限/无法验证项；清洗规则方案渲染
// 流水线 / 处理约定 / 规则清单（按 JSON 实际字段）。空 / 缺失如实标注，不造内容。
// 取数由调用方负责（ArtifactView 经 resources store），本组件只渲染传入内容。
import { computed } from 'vue'
import { NAlert, NTag } from 'naive-ui'
import { formatScore } from '@/components/results/format'
import { formatBool, normalizeScheme } from './scheme'

const props = defineProps<{ content: unknown }>()

const scheme = computed(() => normalizeScheme(props.content))
const rawJson = computed(() =>
  props.content === undefined ? '' : JSON.stringify(props.content, null, 2),
)

function weightText(weight: number | null): string {
  return formatScore(weight)
}
</script>

<template>
  <div class="scheme-view" data-testid="scheme-view">
    <template v-if="scheme">
      <section class="basis-meta" data-testid="basis-meta">
        <div class="basis-meta__title">
          <strong>{{ scheme.schemeId || '（未声明方案 ID）' }}</strong>
          <n-tag size="small" :bordered="false">
            {{ scheme.version ? `v${scheme.version}` : '版本未声明' }}
          </n-tag>
          <n-tag v-if="scheme.status" size="small" :bordered="false">
            {{ scheme.status }}
          </n-tag>
          <n-tag v-if="scheme.configType" size="small" :bordered="false">
            {{ scheme.configType }}
          </n-tag>
        </div>
        <p v-if="scheme.created" class="basis-meta__line">创建：{{ scheme.created }}</p>
        <p v-if="scheme.description" class="basis-meta__line">
          {{ scheme.description }}
        </p>
        <p
          v-if="scheme.operatorLibrary || scheme.engineVersion"
          class="basis-meta__line basis-hint"
        >
          算子库：{{ scheme.operatorLibrary || '—' }} · 引擎：{{
            scheme.engineVersion || '—'
          }}
        </p>
        <p v-if="scheme.engineNotes" class="basis-hint">{{ scheme.engineNotes }}</p>
      </section>

      <!-- 评分方案 -->
      <template v-if="scheme.mode === 'scoring'">
        <section
          v-if="scheme.comparability || scheme.scoreScale"
          class="basis-section"
          data-testid="basis-scope"
        >
          <h4 class="basis-section__title">适用范围</h4>
          <ul class="basis-list" data-testid="basis-scope-list">
            <li v-if="scheme.comparability">
              适用范围：{{ scheme.comparability.validScope || '未声明' }}
            </li>
            <li v-if="scheme.comparability">
              清洗前后同一套公式：{{ formatBool(scheme.comparability.sameFormulas) }}
            </li>
            <li v-if="scheme.comparability">
              跨版本可比：{{ formatBool(scheme.comparability.crossVersion) }}
            </li>
            <li v-if="scheme.scoreScale">
              分值范围：{{ formatScore(scheme.scoreScale.min) }}–{{
                formatScore(scheme.scoreScale.max)
              }}（保留 {{ scheme.scoreScale.rounding ?? '—' }} 位小数）
            </li>
          </ul>
          <n-alert
            v-if="scheme.comparability?.quarantineNote"
            type="warning"
            :bordered="false"
            data-testid="basis-quarantine-note"
          >
            {{ scheme.comparability.quarantineNote }}
          </n-alert>
        </section>

        <section class="basis-section" data-testid="basis-dimensions">
          <h4 class="basis-section__title">
            维度与指标（{{ scheme.dimensions.length }} 个维度）
          </h4>
          <p
            v-if="!scheme.dimensions.length"
            class="basis-hint"
            data-testid="basis-dimensions-empty"
          >
            该方案未声明维度。
          </p>

          <article
            v-for="dim in scheme.dimensions"
            :key="dim.id"
            class="basis-dim"
            :data-testid="`basis-dimension-${dim.id}`"
          >
            <header class="basis-dim__head">
              <h5 class="basis-dim__name">
                {{ dim.nameZh || dim.id }}
                <span class="basis-dim__id">（{{ dim.id }}）</span>
              </h5>
              <n-tag size="small" :bordered="false">
                维度权重 {{ weightText(dim.weight) }}
              </n-tag>
            </header>
            <p v-if="dim.definition" class="basis-dim__def">{{ dim.definition }}</p>

            <ul class="basis-metrics">
              <li
                v-for="metric in dim.metrics"
                :key="metric.id"
                class="basis-metric"
                :data-testid="`basis-metric-${metric.id}`"
              >
                <div class="basis-metric__head">
                  <span class="basis-metric__name">
                    {{ metric.id }} · {{ metric.name || '（未命名指标）' }}
                  </span>
                  <n-tag size="small" :bordered="false">
                    指标权重 {{ weightText(metric.weight) }}
                  </n-tag>
                  <n-tag
                    size="small"
                    :bordered="false"
                    :type="metric.enabled === false ? 'default' : 'success'"
                  >
                    {{ metric.enabled === false ? '停用' : '启用' }}
                  </n-tag>
                  <span v-if="metric.measure" class="basis-metric__measure">
                    {{ metric.measure }}
                  </span>
                </div>
                <p v-if="metric.description" class="basis-metric__desc">
                  {{ metric.description }}
                </p>
                <p class="basis-metric__line">
                  <span class="basis-metric__label">局限：</span>
                  <span
                    class="basis-metric__limit"
                    :data-testid="`basis-metric-${metric.id}-limitation`"
                  >{{ metric.limitations || '—' }}</span>
                </p>
                <p class="basis-metric__line">
                  <span class="basis-metric__label">无法验证：</span>
                  <span
                    v-if="!metric.unverifiable.length"
                    class="basis-hint"
                    :data-testid="`basis-metric-${metric.id}-unverifiable`"
                  >无</span>
                  <span
                    v-else
                    :data-testid="`basis-metric-${metric.id}-unverifiable`"
                  >{{ metric.unverifiable.join('；') }}</span>
                </p>
              </li>
            </ul>
            <p v-if="!dim.metrics.length" class="basis-hint">该维度未声明指标。</p>
          </article>
        </section>

        <section
          v-if="scheme.composite"
          class="basis-section"
          data-testid="basis-composite"
        >
          <h4 class="basis-section__title">综合分</h4>
          <p class="basis-meta__line">
            启用：{{ formatBool(scheme.composite.enabled) }}
          </p>
          <table
            v-if="scheme.composite.weights.length"
            class="basis-table"
            data-testid="basis-composite-weights"
          >
            <tbody>
              <tr v-for="weight in scheme.composite.weights" :key="weight.key">
                <td>{{ weight.label }}</td>
                <td class="basis-num">{{ formatScore(weight.value) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="basis-hint">未声明维度权重。</p>
          <p v-if="scheme.composite.description" class="basis-meta__line">
            {{ scheme.composite.description }}
          </p>
          <p v-if="scheme.composite.editRules" class="basis-hint">
            {{ scheme.composite.editRules }}
          </p>
        </section>
      </template>

      <!-- 清洗规则方案 -->
      <template v-else-if="scheme.mode === 'cleaning'">
        <section
          v-if="scheme.pipeline.length"
          class="basis-section"
          data-testid="basis-pipeline"
        >
          <h4 class="basis-section__title">处理流水线</h4>
          <div
            v-for="stage in scheme.pipeline"
            :key="stage.stage"
            class="basis-pipeline__row"
          >
            <span class="basis-pipeline__stage">{{ stage.stage }}</span>
            <n-tag
              v-for="ruleId in stage.rules"
              :key="ruleId"
              size="small"
              :bordered="false"
            >
              {{ ruleId }}
            </n-tag>
          </div>
        </section>

        <section
          v-if="scheme.conventions.length"
          class="basis-section"
          data-testid="basis-conventions"
        >
          <h4 class="basis-section__title">处理约定</h4>
          <ul class="basis-list">
            <li v-for="item in scheme.conventions" :key="item.id">
              {{ item.id }}：{{ item.description }}
            </li>
          </ul>
        </section>

        <section class="basis-section" data-testid="basis-rules">
          <h4 class="basis-section__title">
            规则清单（{{ scheme.rules.length }} 条）
          </h4>
          <p
            v-if="!scheme.rules.length"
            class="basis-hint"
            data-testid="basis-rules-empty"
          >
            该方案未声明规则。
          </p>
          <table v-else class="basis-table basis-table--rules">
            <thead>
              <tr>
                <th>规则</th>
                <th>名称</th>
                <th>阶段</th>
                <th>表</th>
                <th>级别</th>
                <th>处置</th>
                <th>策略</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="rule in scheme.rules"
                :key="rule.id"
                :data-testid="`basis-rule-${rule.id}`"
              >
                <td>{{ rule.id }}</td>
                <td>{{ rule.name || '—' }}</td>
                <td>{{ rule.stage || '—' }}</td>
                <td>{{ rule.table || '—' }}</td>
                <td>{{ rule.severity || '—' }}</td>
                <td>{{ rule.action || '—' }}</td>
                <td>{{ rule.policyValue || '—' }}</td>
                <td class="basis-rule__desc">{{ rule.description || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>

      <pre v-else class="basis-code" data-testid="basis-raw">{{ rawJson }}</pre>
    </template>

    <pre v-else class="basis-code" data-testid="basis-raw">{{ rawJson }}</pre>
  </div>
</template>

<style scoped>
.scheme-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.basis-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.basis-meta__title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.basis-meta__line {
  margin: 0;
  font-size: var(--fs-md);
  line-height: 1.6;
}
.basis-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.basis-section__title {
  margin: 0;
  font-size: var(--fs-lg);
  font-weight: 600;
}
.basis-list {
  margin: 0;
  padding-left: 20px;
  font-size: var(--fs-md);
  line-height: 1.8;
}
.basis-dim {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.basis-dim__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.basis-dim__name {
  margin: 0;
  font-size: var(--fs-md);
  font-weight: 600;
}
.basis-dim__id {
  color: var(--n-text-color-3, #999);
  font-weight: 400;
}
.basis-dim__def {
  margin: 0;
  font-size: var(--fs-md);
  color: var(--n-text-color-2, #666);
  line-height: 1.6;
}
.basis-metrics {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.basis-metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
}
.basis-metric__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.basis-metric__name {
  font-size: var(--fs-md);
  font-weight: 600;
}
.basis-metric__measure {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fs-sm);
  color: var(--n-text-color-3, #999);
}
.basis-metric__desc {
  margin: 0;
  font-size: var(--fs-md);
  line-height: 1.6;
}
.basis-metric__line {
  margin: 0;
  font-size: var(--fs-md);
  line-height: 1.6;
}
.basis-metric__label {
  color: var(--n-text-color-2, #666);
}
.basis-metric__limit {
  color: #d03050;
}
.basis-pipeline__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.basis-pipeline__stage {
  min-width: 110px;
  font-size: var(--fs-md);
  font-weight: 600;
}
.basis-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-md);
}
.basis-table th,
.basis-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--n-border-color, #f2f3f5);
  text-align: left;
}
.basis-table--rules {
  display: block;
  overflow-x: auto;
}
.basis-rule__desc {
  min-width: 260px;
  max-width: 420px;
}
.basis-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.basis-hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-md);
  line-height: 1.6;
}
.basis-code {
  max-height: 460px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
  font-size: var(--fs-md);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
