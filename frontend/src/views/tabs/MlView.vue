<script setup lang="ts">
// 新前端 · 迭代二模型视图（中栏标签）。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 data-view="ml"
import { useTreeWindowOpen } from '@/composables/useTreeWindowOpen'

const openTree = useTreeWindowOpen()
</script>

<template>
  <section class="view active" data-view="ml" data-testid="view-ml">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>迭代二 · 模型结果</h2>
          <div class="panel-sub">分类 / 聚类 / 降维 · 指标 0–1，前端按需 ×100</div>
        </div>
        <div class="actions">
          <button class="btn sm" data-toast="已复制模型编号">复制 model_id</button>
          <button
            v-if="openTree"
            class="btn sm soft"
            data-testid="ml-open-tree"
            @click="openTree()"
          >
            在对话树中继续
          </button>
        </div>
      </div>
      <div class="ml-grid">
        <div class="card">
          <h3>分类 · 随机森林</h3>
          <p class="panel-sub">ml-20261018-1030-a1b2 · data_version ml1m-clean-v1</p>
          <div class="metric-grid">
            <div class="metric-box"><div class="v">0.7846</div><div class="l">ROC-AUC</div></div>
            <div class="metric-box"><div class="v">0.7123</div><div class="l">F1</div></div>
            <div class="metric-box"><div class="v">0.7312</div><div class="l">PR-AUC</div></div>
          </div>
          <table class="data">
            <tbody>
              <tr><td>Precision / Recall</td><td>0.7041 / 0.7208</td></tr>
              <tr><td>决策阈值</td><td>0.50</td></tr>
              <tr><td>正类比例</td><td>0.5762</td></tr>
              <tr><td>训练 / 验证 / 测试</td><td>972,815 / 14,551 / 12,843</td></tr>
            </tbody>
          </table>
          <div class="confusion">
            <div>TP<b>124,806</b></div>
            <div>FP<b>52,431</b></div>
            <div>FN<b>48,372</b></div>
            <div>TN<b>338,921</b></div>
          </div>
          <p class="ml-note">特征重要性 Top3：用户活跃度 0.182 · 类型偏好熵 0.146 · 平均评分 0.121。防泄漏：只用预测时点之前的数据构建历史特征。</p>
        </div>
        <div class="card">
          <h3>聚类 · K-Means</h3>
          <p class="panel-sub">ml-20261018-1115-c3d4 · k=5 · 用户偏好画像</p>
          <div class="metric-grid">
            <div class="metric-box"><div class="v">0.4213</div><div class="l">轮廓系数</div></div>
            <div class="metric-box"><div class="v">0.8921</div><div class="l">Davies-Bouldin</div></div>
            <div class="metric-box"><div class="v">5</div><div class="l">群体数 k</div></div>
          </div>
          <div class="cluster-row"><b>动作/科幻偏好 · 高活跃 · 评分偏严</b><small>1,284 人 · 类型：Action / Sci-Fi / Thriller</small></div>
          <div class="cluster-row"><b>动画/家庭偏好 · 中活跃 · 评分偏宽</b><small>1,106 人 · 类型：Animation / Children's / Comedy</small></div>
          <div class="cluster-row"><b>剧情/文艺偏好 · 低活跃 · 评分集中</b><small>1,342 人 · 类型：Drama / Romance / War</small></div>
          <div class="cluster-row"><b>恐怖/悬疑偏好 · 高活跃 · 评分波动大</b><small>986 人 · 类型：Horror / Mystery / Film-Noir</small></div>
          <div class="cluster-row"><b>混合口味 · 低活跃 · 评分中性</b><small>1,322 人 · 类型：Comedy / Documentary / Musical</small></div>
        </div>
        <div class="card" style="grid-column:1/-1">
          <h3>降维 · PCA 8 维</h3>
          <p class="panel-sub">ml-20261018-1200-e5f6 · 8 components · 对比实验：全维聚类 vs 降维后聚类</p>
          <table class="data">
            <thead>
              <tr><th>指标</th><th>全维画像</th><th>PCA 8 维</th><th>变化</th></tr>
            </thead>
            <tbody>
              <tr><td>轮廓系数</td><td>0.3864</td><td>0.4213</td><td>+0.0349</td></tr>
              <tr><td>Davies-Bouldin</td><td>0.9412</td><td>0.8921</td><td>−0.0491</td></tr>
              <tr><td>k</td><td>5</td><td>5</td><td>—</td></tr>
            </tbody>
          </table>
          <p class="ml-note">解释方差比 0.6124 · 重构误差 0.1832 · 拟合 3.8 s / 内存 214 MB · 分组一致性 ARI 0.8421 / NMI 0.8064。降维后聚类更紧凑，但信息有损，需结合 ARI/NMI 一起看。</p>
        </div>
      </div>
      <div class="note purple"><b>与对话树的关系：</b>分类和聚类就是从节点 ④ 分出的两支；降维又从分类这一支继续。每条分支保留自己的模型编号、参数和指标，方便对比与复现。</div>
    </div>
  </section>
</template>
