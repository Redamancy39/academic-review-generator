<template>
  <el-card class="result-card" shadow="hover">
    <template #header>
      <div class="card-header">
        <div class="header-left">
          <el-icon><Document /></el-icon>
          <span>综述预览</span>
        </div>
        <div class="header-actions">
          <el-dropdown v-if="taskId" trigger="click" @command="handleExport">
            <el-button type="success" size="small">
              <el-icon><FolderOpened /></el-icon>
              导出文献
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="selected-xlsx">
                  <el-icon><Document /></el-icon>
                  导出选中文献 (Excel)
                </el-dropdown-item>
                <el-dropdown-item command="all-xlsx">
                  <el-icon><Files /></el-icon>
                  导出全部文献 (Excel)
                </el-dropdown-item>
                <el-dropdown-item command="bibtex">
                  <el-icon><DocumentCopy /></el-icon>
                  导出 BibTeX
                </el-dropdown-item>
                <el-dropdown-item command="csv">
                  <el-icon><Document /></el-icon>
                  导出 CSV
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button type="primary" size="small" @click="handleDownload">
            <el-icon><Download /></el-icon>
            下载
          </el-button>
          <el-button size="small" @click="handleCopy">
            <el-icon><CopyDocument /></el-icon>
            复制
          </el-button>
        </div>
      </div>
    </template>

    <div class="result-content">
      <!-- Token 使用统计 -->
      <div v-if="tokenUsage" class="token-usage-card">
        <div class="token-header">
          <el-icon><Coin /></el-icon>
          <span>Token 消耗统计</span>
        </div>
        <el-row :gutter="20" class="token-stats">
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-value">{{ formatNumber(tokenUsage.total_input_tokens) }}</div>
              <div class="stat-label">输入 Tokens</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-value">{{ formatNumber(tokenUsage.total_output_tokens) }}</div>
              <div class="stat-label">输出 Tokens</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-value">{{ formatNumber(tokenUsage.total_tokens) }}</div>
              <div class="stat-label">总计 Tokens</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="stat-item">
              <div class="stat-value cost">{{ formatCost(tokenUsage.estimated_cost) }}</div>
              <div class="stat-label">估算费用</div>
            </div>
          </el-col>
        </el-row>
        <el-collapse v-if="tokenUsage.stages && Object.keys(tokenUsage.stages).length > 0" class="stages-collapse">
          <el-collapse-item title="各阶段详情" name="stages">
            <el-table :data="stagesData" size="small" border>
              <el-table-column prop="stage" label="阶段" width="150" />
              <el-table-column prop="input" label="输入" width="100">
                <template #default="{ row }">{{ formatNumber(row.input) }}</template>
              </el-table-column>
              <el-table-column prop="output" label="输出" width="100">
                <template #default="{ row }">{{ formatNumber(row.output) }}</template>
              </el-table-column>
              <el-table-column prop="total" label="总计" width="100">
                <template #default="{ row }">{{ formatNumber(row.total) }}</template>
              </el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- 质量验证 -->
      <div v-if="validation" class="validation-info">
        <el-tag :type="validation.passes ? 'success' : 'warning'">
          {{ validation.passes ? '质量检测通过' : '质量检测未完全通过' }}
        </el-tag>
        <span class="word-count">
          正文字数: {{ validation.word_count }}
          <el-tooltip v-if="validation.word_count_with_refs" content="不含参考文献" placement="top">
            <el-icon><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="ref-count">参考文献: {{ validation.unique_citation_count }} 篇</span>
      </div>

      <el-divider />

      <div class="markdown-content" v-html="renderedMarkdown" />
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import { ElMessage } from 'element-plus'
import { exportApi } from '@/api'

const props = defineProps({
  markdown: {
    type: String,
    default: '',
  },
  validation: {
    type: Object,
    default: null,
  },
  tokenUsage: {
    type: Object,
    default: null,
  },
  taskId: {
    type: String,
    default: '',
  },
})

const renderedMarkdown = computed(() => {
  if (!props.markdown) return '<p class="placeholder">暂无内容</p>'
  return marked(props.markdown)
})

const stagesData = computed(() => {
  if (!props.tokenUsage?.stages) return []
  return Object.entries(props.tokenUsage.stages).map(([stage, data]) => ({
    stage: formatStageName(stage),
    input: data.input_tokens || 0,
    output: data.output_tokens || 0,
    total: data.total_tokens || 0,
  }))
})

const formatNumber = (num) => {
  if (!num) return '0'
  return num.toLocaleString()
}

const formatCost = (cost) => {
  if (!cost) return '¥0.00'
  return `¥${cost.toFixed(4)}`
}

const formatStageName = (stage) => {
  const stageNames = {
    planning: '规划',
    retrieval: '检索',
    screening: '筛选',
    analysis: '分析',
    writing: '撰写',
    review: '审稿',
    revision: '修订',
  }
  // 处理带编号的阶段名
  for (const [key, name] of Object.entries(stageNames)) {
    if (stage.startsWith(key)) {
      const suffix = stage.replace(key, '')
      return name + (suffix ? ` (${suffix.replace(/_/g, ' ')})` : '')
    }
  }
  return stage
}

const handleDownload = () => {
  if (!props.markdown) {
    ElMessage.warning('暂无内容可下载')
    return
  }

  const blob = new Blob([props.markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `review_${Date.now()}.md`
  link.click()
  URL.revokeObjectURL(url)
  ElMessage.success('下载成功')
}

const handleCopy = async () => {
  if (!props.markdown) {
    ElMessage.warning('暂无内容可复制')
    return
  }

  try {
    await navigator.clipboard.writeText(props.markdown)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败')
  }
}

const handleExport = (command) => {
  if (!props.taskId) {
    ElMessage.warning('任务ID不存在')
    return
  }

  switch (command) {
    case 'selected-xlsx':
      exportApi.downloadSelectedXlsx(props.taskId)
      ElMessage.success('正在导出选中文献...')
      break
    case 'all-xlsx':
      exportApi.downloadPapersXlsx(props.taskId)
      ElMessage.success('正在导出全部文献...')
      break
    case 'bibtex':
      exportApi.downloadBibtex(props.taskId)
      ElMessage.success('正在导出 BibTeX...')
      break
    case 'csv':
      exportApi.downloadCsv(props.taskId)
      ElMessage.success('正在导出 CSV...')
      break
  }
}
</script>

<style scoped>
.result-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.token-usage-card {
  background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%);
  border: 1px solid #667eea30;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}

.token-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #667eea;
}

.token-stats {
  text-align: center;
}

.stat-item {
  padding: 8px;
}

.stat-value {
  font-size: 20px;
  font-weight: 700;
  color: #303133;
}

.stat-value.cost {
  color: #67c23a;
}

.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.stages-collapse {
  margin-top: 12px;
}

.validation-info {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-bottom: 10px;
}

.word-count, .ref-count {
  color: #606266;
  font-size: 13px;
}

.markdown-content {
  max-height: 600px;
  overflow-y: auto;
  padding: 10px;
  background: #fafafa;
  border-radius: 4px;
}

.markdown-content :deep(h1) {
  font-size: 24px;
  border-bottom: 1px solid #eee;
  padding-bottom: 10px;
}

.markdown-content :deep(h2) {
  font-size: 20px;
  margin-top: 20px;
}

.markdown-content :deep(h3) {
  font-size: 16px;
  margin-top: 15px;
}

.markdown-content :deep(p) {
  line-height: 1.8;
  margin: 10px 0;
}

.markdown-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 15px 0;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid #ebeef5;
  padding: 8px 12px;
  text-align: left;
}

.markdown-content :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}

.placeholder {
  color: #909399;
  text-align: center;
  padding: 40px;
}
</style>
