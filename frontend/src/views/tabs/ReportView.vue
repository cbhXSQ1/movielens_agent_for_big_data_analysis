<script setup lang="ts">
// 新前端 · 评估报告.md 视图（中栏标签）。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 data-view="report"
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'

const tasks = useTasksStore()
const { currentResult } = storeToRefs(tasks)

const reportText = computed(() => {
  const blocks = currentResult.value?.blocks as { report?: string } | undefined
  return blocks?.report || ''
})

const hasReport = computed(() => reportText.value.length > 0)
</script>

<template>
  <section class="view active" data-view="report" data-testid="view-report">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>评估报告.md</h2>
          <div class="panel-sub">report.md · 由 Hadoop driver 产出</div>
        </div>
        <div class="actions">
          <button class="btn sm" data-toast="已复制 Markdown">复制</button>
          <button class="btn sm" data-toast="演示环境不产生真实下载">下载 .md</button>
        </div>
      </div>

      <div v-if="hasReport" class="card md">
        <pre style="white-space: pre-wrap">{{ reportText }}</pre>
      </div>

      <div v-else class="card md">
        <h1>MovieLens 1M 清洗与质量评估报告</h1>
        <p>
          <span class="tag blue">ml1m-clean-v1</span>
          <span class="tag">cleaning_rules 1.0.0</span>
          <span class="tag">scoring_scheme 1.0.0</span>
        </p>
        <h2>1. 数据量变化</h2>
        <table>
          <thead>
            <tr><th>表</th><th>清洗前</th><th>清洗后</th><th>变化</th></tr>
          </thead>
          <tbody>
            <tr><td>ratings</td><td>1,150,241</td><td>1,000,209</td><td>−150,032</td></tr>
            <tr><td>users</td><td>6,946</td><td>6,040</td><td>−906</td></tr>
            <tr><td>movies</td><td>4,465</td><td>3,883</td><td>−582</td></tr>
          </tbody>
        </table>
        <h2>2. 五维评分</h2>
        <table>
          <thead>
            <tr><th>维度</th><th>清洗前</th><th>清洗后</th><th>变化</th></tr>
          </thead>
          <tbody>
            <tr><td>准确性 Accurate</td><td>97.71</td><td>99.79</td><td>+2.08</td></tr>
            <tr><td>完整性 Complete</td><td>98.82</td><td>99.99</td><td>+1.17</td></tr>
            <tr><td>唯一性 Unique</td><td>90.90</td><td>100</td><td>+9.10</td></tr>
            <tr><td>时效性 Up-to-date</td><td>98.22</td><td>100</td><td>+1.78</td></tr>
            <tr><td>一致性 Consistent</td><td>89.65</td><td>100</td><td>+10.35</td></tr>
            <tr><td><b>综合分</b></td><td><b>95.07</b></td><td><b>99.95</b></td><td><b>+4.88</b></td></tr>
          </tbody>
        </table>
        <h2>3. 处理了哪些问题</h2>
        <ul>
          <li>文本编码修复（P1）：48 行；</li>
          <li>结构异常隔离（P2/P3/R1/R2/R3/R5）：按规则计入隔离区；</li>
          <li>时间戳毫秒补全（R4）：13,503 行；</li>
          <li>评分去重（R6）：49,510 行；</li>
          <li>跨表引用校验（X1/X2）：10,502 行隔离；</li>
          <li>用户/电影维表去重与修复：U5 726 行、M4 454 行、M2 47 行、U3 73 行。</li>
        </ul>
        <h2>4. 评价局限</h2>
        <ul>
          <li>用户属性为自愿填写、未经核验，准确性与完整性不封顶；</li>
          <li>分数提升有一部分来自把无法判定的记录移出分母，不等于这些问题被修复；</li>
          <li>当前为单机伪分布式部署，未做多机验证；</li>
          <li>"评分是否真实反映用户偏好"无法从数据本身判定。</li>
        </ul>
        <blockquote>隔离 ≠ 修复：隔离 100,830 行、去重 49,510 行、修复 13,503 行，三项独立统计；报告同屏披露数据量变化。</blockquote>
        <p><code>本报告由 Hadoop driver 产出，数字来自真实运行；前端只负责展示，不生成数字。</code></p>
      </div>
    </div>
  </section>
</template>
