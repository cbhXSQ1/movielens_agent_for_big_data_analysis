<script setup lang="ts">
// 新前端 · 方案依据视图（中栏标签）。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 data-view="basis"
</script>

<template>
  <section class="view active" data-view="basis" data-testid="view-basis">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>方案依据</h2>
          <div class="panel-sub">清洗规则 · 评分方案 · 时间边界</div>
        </div>
        <div class="actions">
          <button class="btn sm" data-toast="已复制方案版本">复制版本</button>
        </div>
      </div>
      <div class="basis-grid">
        <div class="card">
          <h3>清洗方案 · cleaning_rules.v1.json</h3>
          <p class="panel-sub">v1.0.0 · 已登记默认方案 · 只读</p>
          <ul class="basis-list">
            <li><b>6 个阶段：</b>解析校验 → 修复 → 去重 → 冲突处理 → 跨表校验 → 报告</li>
            <li><b>31 个算子：</b>detect 只能用已登记算子，禁止自由代码</li>
            <li><b>处理约定：</b>ISO-8859-1、<code>::</code> 分隔、无表头、邮编按字符串</li>
            <li><b>版本保护：</b>默认方案不可覆盖，只能新增版本</li>
          </ul>
        </div>
        <div class="card">
          <h3>评分方案 · scoring_scheme.v1.json</h3>
          <p class="panel-sub">v1.0.0 · 五个维度固定 · 18 项指标</p>
          <ul class="basis-list">
            <li><b>准确性 0.25：</b>值域、时间戳可解析、属性编码、外键引用</li>
            <li><b>完整性 0.20：</b>必填字段、三表关联、用户属性</li>
            <li><b>唯一性 0.15：</b>评分/用户/电影记录唯一</li>
            <li><b>时效性 0.20：</b>时间戳范围、时间边界覆盖</li>
            <li><b>一致性 0.20：</b>跨表引用、类型枚举、数值格式、画像一致</li>
          </ul>
        </div>
        <div class="card">
          <h3>时间边界</h3>
          <ul class="basis-list">
            <li><b>T1 训练期截止：</b>2001-12-31 23:59:59 UTC</li>
            <li><b>T2 验证期截止：</b>2002-06-30 23:59:59 UTC</li>
            <li><b>训练：</b>Timestamp ≤ T1</li>
            <li><b>验证：</b>T1 &lt; Timestamp ≤ T2</li>
            <li><b>测试：</b>Timestamp &gt; T2</li>
          </ul>
        </div>
        <div class="card">
          <h3>版本与可追溯</h3>
          <ul class="basis-list">
            <li><b>数据版本：</b>ml1m-clean-v1</li>
            <li><b>规则版本：</b>1.0.0（sha256 写入任务元数据）</li>
            <li><b>评分版本：</b>1.0.0（sha256 写入任务元数据）</li>
            <li><b>发布：</b>HDFS 内容哈希清单，同版本不一致直接报冲突</li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>
