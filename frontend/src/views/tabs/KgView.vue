<script setup lang="ts">
// 新前端 · 知识图谱视图（中栏标签）。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 data-view="kg"
import { useTreeWindowOpen } from '@/composables/useTreeWindowOpen'

const openTree = useTreeWindowOpen()
</script>

<template>
  <section class="view active" data-view="kg" data-testid="view-kg">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>电影知识图谱</h2>
          <div class="panel-sub">迭代三预览 · 实体 / 关系 / 路径</div>
        </div>
        <div class="actions">
          <button
            v-if="openTree"
            class="btn sm soft"
            data-testid="kg-open-tree"
            @click="openTree()"
          >
            在对话树中继续
          </button>
        </div>
      </div>
      <div class="kg-wrap">
        <div class="kg-canvas">
          <svg viewBox="0 0 760 460" aria-label="电影知识图谱示意">
            <line x1="210" y1="120" x2="390" y2="90" stroke="#CBD5E1" stroke-width="2" />
            <line x1="210" y1="120" x2="390" y2="210" stroke="#CBD5E1" stroke-width="2" />
            <line x1="210" y1="120" x2="390" y2="330" stroke="#CBD5E1" stroke-width="2" />
            <line x1="390" y1="90" x2="590" y2="150" stroke="#CBD5E1" stroke-width="2" />
            <line x1="390" y1="210" x2="590" y2="150" stroke="#CBD5E1" stroke-width="2" />
            <line x1="390" y1="330" x2="590" y2="150" stroke="#CBD5E1" stroke-width="2" />
            <line x1="590" y1="150" x2="660" y2="300" stroke="#CBD5E1" stroke-width="2" />
            <line x1="390" y1="210" x2="590" y2="300" stroke="#CBD5E1" stroke-width="2" />
            <line x1="390" y1="330" x2="590" y2="300" stroke="#CBD5E1" stroke-width="2" />
            <g>
              <circle cx="210" cy="120" r="46" fill="#E9F1FF" stroke="#3D7EF2" stroke-width="2" />
              <text x="210" y="116" text-anchor="middle" font-size="13" font-weight="700" fill="#1F4FA8">Toy Story</text>
              <text x="210" y="134" text-anchor="middle" font-size="10" fill="#5F6472">Movie</text>
            </g>
            <g>
              <circle cx="390" cy="90" r="40" fill="#EFEAFD" stroke="#8B5CF0" stroke-width="2" />
              <text x="390" y="86" text-anchor="middle" font-size="11" font-weight="700" fill="#5B34B0">Animation</text>
              <text x="390" y="102" text-anchor="middle" font-size="10" fill="#5F6472">Genre</text>
            </g>
            <g>
              <circle cx="390" cy="210" r="44" fill="#E2F4EB" stroke="#279268" stroke-width="2" />
              <text x="390" y="206" text-anchor="middle" font-size="11" font-weight="700" fill="#16603F">高活跃动画偏好</text>
              <text x="390" y="222" text-anchor="middle" font-size="10" fill="#5F6472">User cluster</text>
            </g>
            <g>
              <circle cx="390" cy="330" r="42" fill="#FFF3DD" stroke="#A5650A" stroke-width="2" />
              <text x="390" y="326" text-anchor="middle" font-size="11" font-weight="700" fill="#6B4300">≥4 分评分</text>
              <text x="390" y="342" text-anchor="middle" font-size="10" fill="#5F6472">Rating</text>
            </g>
            <g>
              <circle cx="590" cy="150" r="42" fill="#E9F1FF" stroke="#3D7EF2" stroke-width="2" />
              <text x="590" y="146" text-anchor="middle" font-size="11" font-weight="700" fill="#1F4FA8">A Bug's Life</text>
              <text x="590" y="162" text-anchor="middle" font-size="10" fill="#5F6472">Movie</text>
            </g>
            <g>
              <circle cx="590" cy="300" r="40" fill="#EFEAFD" stroke="#8B5CF0" stroke-width="2" />
              <text x="590" y="296" text-anchor="middle" font-size="11" font-weight="700" fill="#5B34B0">Children's</text>
              <text x="590" y="312" text-anchor="middle" font-size="10" fill="#5F6472">Genre</text>
            </g>
            <g>
              <circle cx="680" cy="300" r="36" fill="#E2F4EB" stroke="#279268" stroke-width="2" />
              <text x="680" y="296" text-anchor="middle" font-size="10" font-weight="700" fill="#16603F">12 部候选</text>
              <text x="680" y="312" text-anchor="middle" font-size="10" fill="#5F6472">Result</text>
            </g>
            <text x="300" y="98" font-size="10" fill="#6E7280">属于类型</text>
            <text x="300" y="196" font-size="10" fill="#6E7280">偏好</text>
            <text x="300" y="318" font-size="10" fill="#6E7280">评分关系</text>
            <text x="490" y="112" font-size="10" fill="#6E7280">共同类型</text>
            <text x="490" y="296" font-size="10" fill="#6E7280">属于类型</text>
          </svg>
        </div>
        <div class="kg-side">
          <div class="card">
            <h3>这次探索</h3>
            <ul class="basis-list">
              <li>起点：电影《Toy Story》</li>
              <li>关系：属于类型 → Animation</li>
              <li>扩展：共同类型 + 高评分</li>
              <li>结果：12 部候选</li>
            </ul>
          </div>
          <div class="note purple"><b>为什么需要树：</b>图探索很少一条路走到底。从某个实体回到上一步，换一条关系继续，是迭代三的常态；旧路径保留才能对比与复现。</div>
        </div>
      </div>
    </div>
  </section>
</template>
