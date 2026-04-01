<template>
  <el-card class="progress-card" shadow="hover">
    <template #header>
      <div class="card-header">
        <el-icon><Odometer /></el-icon>
        <span>执行进度</span>
        <el-tag v-if="status === 'running'" type="warning" size="small">运行中</el-tag>
        <el-tag v-else-if="status === 'completed'" type="success" size="small">已完成</el-tag>
        <el-tag v-else-if="status === 'failed'" type="danger" size="small">失败</el-tag>
      </div>
    </template>

    <div class="progress-content">
      <el-steps :active="currentStep" align-center>
        <el-step v-for="step in steps" :key="step.key" :title="step.title" :description="step.description" />
      </el-steps>

      <div class="progress-bar-container">
        <el-progress
          :percentage="progressPercent"
          :status="progressStatus"
          :stroke-width="20"
          striped
          striped-flow
        />
        <p class="progress-message">{{ message }}</p>
      </div>

      <!-- 数据源进度 -->
      <div v-if="showSourcesProgress" class="sources-progress">
        <div class="sources-header">
          <el-icon><Connection /></el-icon>
          <span>数据源检索进度</span>
        </div>
        <div class="sources-list">
          <div
            v-for="(sourceData, sourceName) in sourcesProgress"
            :key="sourceName"
            class="source-item"
            :class="{ active: currentSource === sourceName }"
          >
            <div class="source-name">
              <el-icon v-if="sourceData.status === 'completed'" class="status-icon success"><CircleCheck /></el-icon>
              <el-icon v-else-if="sourceData.status === 'failed'" class="status-icon error"><CircleClose /></el-icon>
              <el-icon v-else-if="sourceData.status === 'running'" class="status-icon running"><Loading /></el-icon>
              <el-icon v-else class="status-icon pending"><Clock /></el-icon>
              <span>{{ sourceName }}</span>
            </div>
            <div class="source-status">
              <span v-if="sourceData.status === 'running'" class="running-text">检索中...</span>
              <span v-else-if="sourceData.status === 'completed'" class="completed-text">{{ sourceData.count }} 篇</span>
              <span v-else-if="sourceData.status === 'failed'" class="failed-text">失败</span>
              <span v-else class="pending-text">等待中</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 主题解析结果 -->
      <div v-if="topicAnalysis && topicAnalysis.keywords?.length" class="stage-data-section">
        <div class="section-header">
          <el-icon><Collection /></el-icon>
          <span>主题解析结果</span>
        </div>
        <div class="topic-analysis-content">
          <div class="analysis-item">
            <span class="label">领域：</span>
            <el-tag size="small">{{ topicAnalysis.domain }}</el-tag>
          </div>
          <div v-if="topicAnalysis.sub_domains?.length" class="analysis-item">
            <span class="label">子领域：</span>
            <el-tag v-for="sub in topicAnalysis.sub_domains" :key="sub" size="small" type="info">{{ sub }}</el-tag>
          </div>
          <div class="analysis-item">
            <span class="label">关键词：</span>
            <div class="keywords-list">
              <el-tag v-for="kw in topicAnalysis.keywords" :key="kw" size="small" type="success">{{ kw }}</el-tag>
            </div>
          </div>
        </div>
      </div>

      <!-- 章节结构 -->
      <div v-if="planSections && planSections.length" class="stage-data-section">
        <div class="section-header">
          <el-icon><Document /></el-icon>
          <span>综述章节结构</span>
        </div>
        <el-table :data="planSections" size="small" stripe>
          <el-table-column prop="title" label="章节标题" min-width="120" />
          <el-table-column prop="target_words" label="目标字数" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ row.target_words }} 字</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="goal" label="写作目标" min-width="200" show-overflow-tooltip />
        </el-table>
      </div>

      <!-- 检索到的文献 -->
      <div v-if="retrievedPapers && retrievedPapers.length" class="stage-data-section">
        <div class="section-header">
          <el-icon><Files /></el-icon>
          <span>检索到的文献</span>
          <el-tag v-if="totalRetrieved" size="small" type="info">共 {{ totalRetrieved }} 篇</el-tag>
        </div>
        <div class="papers-list">
          <el-collapse accordion>
            <el-collapse-item
              v-for="paper in retrievedPapers.slice(0, 10)"
              :key="paper.ref_id"
              :name="paper.ref_id"
            >
              <template #title>
                <div class="paper-title-row">
                  <el-tag size="small" type="primary">{{ paper.ref_id }}</el-tag>
                  <span class="paper-title">{{ paper.title }}</span>
                  <el-tag v-if="paper.jcr_quartile" size="small" :type="getQuartileType(paper.jcr_quartile)">
                    {{ paper.jcr_quartile }}
                  </el-tag>
                </div>
              </template>
              <div class="paper-detail">
                <p><strong>年份：</strong>{{ paper.year || 'N/A' }}</p>
                <p><strong>期刊：</strong>{{ paper.journal || 'N/A' }}</p>
                <p><strong>相关性：</strong>{{ paper.relevance_score?.toFixed(1) || 'N/A' }}</p>
                <p v-if="paper.abstract_preview"><strong>摘要：</strong>{{ paper.abstract_preview }}...</p>
              </div>
            </el-collapse-item>
          </el-collapse>
          <p v-if="retrievedPapers.length > 10" class="more-hint">
            还有 {{ retrievedPapers.length - 10 }} 篇文献未展示...
          </p>
        </div>
      </div>

      <!-- 筛选后的文献 -->
      <div v-if="selectedPapers && selectedPapers.length" class="stage-data-section">
        <div class="section-header">
          <el-icon><Select /></el-icon>
          <span>筛选后的核心文献</span>
          <el-tag v-if="totalSelected" size="small" type="success">共 {{ totalSelected }} 篇</el-tag>
        </div>
        <div class="papers-list">
          <el-collapse accordion>
            <el-collapse-item
              v-for="paper in selectedPapers.slice(0, 15)"
              :key="paper.ref_id"
              :name="paper.ref_id"
            >
              <template #title>
                <div class="paper-title-row">
                  <el-tag size="small" type="success">{{ paper.ref_id }}</el-tag>
                  <span class="paper-title">{{ paper.title }}</span>
                  <el-tag v-if="paper.jcr_quartile" size="small" :type="getQuartileType(paper.jcr_quartile)">
                    {{ paper.jcr_quartile }}
                  </el-tag>
                </div>
              </template>
              <div class="paper-detail">
                <p><strong>年份：</strong>{{ paper.year || 'N/A' }}</p>
                <p><strong>期刊：</strong>{{ paper.journal || 'N/A' }}</p>
                <p><strong>相关性：</strong>{{ paper.relevance_score?.toFixed(1) || 'N/A' }}</p>
                <p v-if="paper.abstract_preview"><strong>摘要：</strong>{{ paper.abstract_preview }}...</p>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>

      <!-- 草稿预览 -->
      <div v-if="draftPreview" class="stage-data-section">
        <div class="section-header">
          <el-icon><Edit /></el-icon>
          <span>当前稿件预览</span>
        </div>
        <div class="draft-preview">
          <pre>{{ draftPreview }}</pre>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  stage: {
    type: String,
    default: 'init',
  },
  progress: {
    type: Number,
    default: 0,
  },
  message: {
    type: String,
    default: '',
  },
  status: {
    type: String,
    default: 'running',
  },
  currentSource: {
    type: String,
    default: null,
  },
  sourcesProgress: {
    type: Object,
    default: null,
  },
  // 新增：中间数据
  topicAnalysis: {
    type: Object,
    default: null,
  },
  planSections: {
    type: Array,
    default: null,
  },
  retrievedPapers: {
    type: Array,
    default: null,
  },
  selectedPapers: {
    type: Array,
    default: null,
  },
  draftPreview: {
    type: String,
    default: null,
  },
  totalRetrieved: {
    type: Number,
    default: 0,
  },
  totalSelected: {
    type: Number,
    default: 0,
  },
})

const steps = [
  { key: 'init', title: '初始化', description: '准备中' },
  { key: 'planning', title: '规划', description: '生成框架' },
  { key: 'retrieval', title: '检索', description: '获取文献' },
  { key: 'screening', title: '筛选', description: '筛选文献' },
  { key: 'analysis', title: '分析', description: '提取证据' },
  { key: 'writing', title: '撰写', description: '生成综述' },
  { key: 'review', title: '审稿', description: '修订完善' },
  { key: 'complete', title: '完成', description: '输出结果' },
]

const stageOrder = steps.map(s => s.key)

const currentStep = computed(() => {
  const index = stageOrder.indexOf(props.stage)
  return index >= 0 ? index : 0
})

const progressPercent = computed(() => {
  return Math.round(props.progress * 100)
})

const progressStatus = computed(() => {
  if (props.status === 'completed') return 'success'
  if (props.status === 'failed') return 'exception'
  return undefined
})

const showSourcesProgress = computed(() => {
  return props.stage === 'retrieval' && props.sourcesProgress && Object.keys(props.sourcesProgress).length > 0
})

const getQuartileType = (quartile) => {
  if (quartile === 'Q1') return 'success'
  if (quartile === 'Q2') return 'primary'
  if (quartile === 'Q3') return 'warning'
  if (quartile === 'Q4') return 'info'
  return ''
}
</script>

<style scoped>
.progress-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.progress-content {
  padding: 20px 0;
}

.progress-bar-container {
  margin-top: 30px;
  text-align: center;
}

.progress-message {
  margin-top: 15px;
  color: #606266;
  font-size: 14px;
}

/* 数据源进度样式 */
.sources-progress {
  margin-top: 20px;
  padding: 15px;
  background: #f5f7fa;
  border-radius: 8px;
}

.sources-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-weight: 600;
  color: #606266;
}

.sources-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.source-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background: #fff;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
  transition: all 0.3s ease;
}

.source-item.active {
  border-color: #409eff;
  box-shadow: 0 0 8px rgba(64, 158, 255, 0.2);
}

.source-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
}

.status-icon {
  font-size: 16px;
}

.status-icon.success { color: #67c23a; }
.status-icon.error { color: #f56c6c; }
.status-icon.running { color: #409eff; animation: spin 1s linear infinite; }
.status-icon.pending { color: #909399; }

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.source-status { font-size: 13px; }
.running-text { color: #409eff; font-weight: 500; }
.completed-text { color: #67c23a; font-weight: 500; }
.failed-text { color: #f56c6c; }
.pending-text { color: #909399; }

/* 阶段数据展示区域 */
.stage-data-section {
  margin-top: 20px;
  padding: 15px;
  background: #f5f7fa;
  border-radius: 8px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-weight: 600;
  color: #303133;
}

/* 主题解析 */
.topic-analysis-content {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.analysis-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.analysis-item .label {
  color: #606266;
  font-size: 13px;
  min-width: 60px;
  padding-top: 2px;
}

.keywords-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 文献列表 */
.papers-list {
  max-height: 400px;
  overflow-y: auto;
}

.paper-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.paper-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.paper-detail {
  padding: 10px;
  background: #fff;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
}

.paper-detail p {
  margin: 4px 0;
}

.more-hint {
  text-align: center;
  color: #909399;
  font-size: 12px;
  margin-top: 10px;
}

/* 草稿预览 */
.draft-preview {
  background: #fff;
  padding: 15px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
  max-height: 300px;
  overflow-y: auto;
}

.draft-preview pre {
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: inherit;
}
</style>
