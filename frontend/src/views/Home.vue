<template>
  <div class="home-page">
    <!-- 上部分：左右两列布局 -->
    <el-row :gutter="20">
      <!-- 左侧：主题输入和参数配置 -->
      <el-col :span="12">
        <TopicInput
          :is-loading="store.isLoading"
          :is-running="isRunning"
          @submit="handleSubmit"
        />

        <ParameterConfig v-if="showConfig" :config="currentConfig" />

        <AgentPreview v-if="store.agentDefinitions" :agents="store.agentDefinitions" />
      </el-col>

      <!-- 右侧：进度监控 -->
      <el-col :span="12">
        <ProgressMonitor
          v-if="store.currentTask"
          :stage="store.currentTask.current_stage"
          :progress="store.currentTask.progress"
          :message="store.currentTask.message"
          :status="store.currentTask.status"
          :current-source="store.currentTask.current_source"
          :sources-progress="store.currentTask.sources_progress"
          :topic-analysis="store.currentTask.topic_analysis"
          :plan-sections="store.currentTask.plan_sections"
          :retrieved-papers="store.currentTask.retrieved_papers"
          :selected-papers="store.currentTask.selected_papers"
          :draft-preview="store.currentTask.draft_preview"
          :total-retrieved="store.currentTask.total_retrieved"
          :total-selected="store.currentTask.total_selected"
        />

        <!-- 使用说明 -->
        <el-card v-if="!store.currentTask" class="guide-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <el-icon><InfoFilled /></el-icon>
              <span>使用说明</span>
            </div>
          </template>
          <el-timeline>
            <el-timeline-item>
              <h4>输入研究主题</h4>
              <p>在左侧输入您要生成综述的研究主题，建议包含领域和具体方向</p>
            </el-timeline-item>
            <el-timeline-item>
              <h4>配置参数</h4>
              <p>根据需要调整字数、参考文献数、年份窗口等参数</p>
            </el-timeline-item>
            <el-timeline-item>
              <h4>选择模式</h4>
              <p>全自动模式：一键生成 | 半自动模式：每个阶段人工审核</p>
            </el-timeline-item>
            <el-timeline-item>
              <h4>生成综述</h4>
              <p>系统将自动进行文献检索、筛选、分析和撰写</p>
            </el-timeline-item>
            <el-timeline-item>
              <h4>下载结果</h4>
              <p>生成完成后可下载Markdown格式文档</p>
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </el-col>
    </el-row>

    <!-- 底部：结果预览（全宽） -->
    <div v-if="result" class="result-section">
      <ResultPreview
        :markdown="result.final_markdown"
        :validation="result.validation"
        :token-usage="result.token_usage"
        :task-id="currentTaskId"
      />
    </div>

    <!-- 半自动模式：审核确认对话框 -->
    <StageConfirmDialog
      v-model="showConfirmDialog"
      :pause-reason="pauseReason"
      :topic-analysis="store.currentTask?.topic_analysis"
      :plan-sections="store.currentTask?.plan_sections"
      :selected-papers="selectedPapersForDialog"
      :candidate-papers="candidatePapersForDialog"
      :total-retrieved="store.currentTask?.total_retrieved || 0"
      :target-refs="currentConfig?.target_refs || 40"
      :task-id="currentTaskId"
      @confirm="handleConfirm"
      @revise="handleRevise"
      @abort="handleAbort"
    />
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useReviewStore } from '@/stores/review'
import { reviewApi } from '@/api'
import TopicInput from '@/components/TopicInput.vue'
import ParameterConfig from '@/components/ParameterConfig.vue'
import ProgressMonitor from '@/components/ProgressMonitor.vue'
import ResultPreview from '@/components/ResultPreview.vue'
import AgentPreview from '@/components/AgentPreview.vue'
import StageConfirmDialog from '@/components/StageConfirmDialog.vue'

const router = useRouter()
const store = useReviewStore()

const currentConfig = ref(null)
const result = ref(null)
const pollInterval = ref(null)
const isRunning = ref(false) // 防止重复点击的状态
const currentTaskId = ref('') // 当前任务ID，用于导出文献

// 半自动模式状态
const showConfirmDialog = ref(false)
const pauseReason = ref('')

const showConfig = computed(() => currentConfig.value !== null)

// 为对话框准备的文献数据
const selectedPapersForDialog = computed(() => {
  return store.currentTask?.selected_papers || []
})

const candidatePapersForDialog = computed(() => {
  // 从检索到的文献中排除已选中的
  const selectedIds = new Set(store.currentTask?.selected_papers?.map(p => p.ref_id) || [])
  return (store.currentTask?.retrieved_papers || []).filter(p => !selectedIds.has(p.ref_id))
})

const handleSubmit = async (formData) => {
  // 防止重复点击
  if (isRunning.value) {
    ElMessage.warning('任务正在执行中，请等待完成')
    return
  }

  try {
    isRunning.value = true
    currentConfig.value = formData
    result.value = null // 重置结果

    // 分析主题
    ElMessage.info('正在分析主题...')
    const analysis = await store.analyzeTopic(formData.topic)

    // 生成Agent定义
    ElMessage.info('正在生成Agent配置...')
    await store.generateAgents({
      topic: formData.topic,
      domain: analysis.domain,
      keywords: analysis.keywords,
      sub_domains: analysis.sub_domains,
      word_count_min: formData.word_count_min,
      word_count_max: formData.word_count_max,
      target_refs: formData.target_refs,
      year_window: formData.year_window,
    })

    // 创建任务（传递模式参数）
    ElMessage.info('正在创建任务...')
    const task = await store.createReview(formData)

    currentTaskId.value = task.task_id
    const modeText = formData.mode === 'semi-auto' ? '半自动模式' : '全自动模式'
    ElMessage.success(`任务创建成功（${modeText}），开始执行...`)

    // 开始轮询任务状态
    startPolling(task.task_id)
  } catch (error) {
    isRunning.value = false
    ElMessage.error(error.message || '操作失败')
  }
}

const startPolling = (taskId) => {
  // 清除之前的轮询
  if (pollInterval.value) {
    clearInterval(pollInterval.value)
  }

  // 每2秒轮询一次
  pollInterval.value = setInterval(async () => {
    try {
      const status = await store.getTaskStatus(taskId)

      // 检查是否暂停（半自动模式）
      if (status.is_paused && status.awaiting_user_action) {
        pauseReason.value = status.pause_reason
        showConfirmDialog.value = true
        // 暂停轮询，等待用户操作
        clearInterval(pollInterval.value)
        pollInterval.value = null
        return
      }

      if (status.status === 'completed') {
        clearInterval(pollInterval.value)
        pollInterval.value = null
        isRunning.value = false

        // 获取结果
        result.value = await store.getTaskResult(taskId)
        ElMessage.success('综述生成完成！')
      } else if (status.status === 'failed' || status.status === 'aborted') {
        clearInterval(pollInterval.value)
        pollInterval.value = null
        isRunning.value = false
        if (status.status === 'failed') {
          ElMessage.error('任务执行失败')
        } else {
          ElMessage.warning('任务已终止')
        }
      }
    } catch (error) {
      console.error('轮询失败:', error)
    }
  }, 2000)
}

// 半自动模式：确认继续
const handleConfirm = async () => {
  try {
    await reviewApi.confirm(currentTaskId.value, { action: 'continue' })
    showConfirmDialog.value = false
    ElMessage.success('已确认，继续执行...')
    // 恢复轮询
    startPolling(currentTaskId.value)
  } catch (error) {
    ElMessage.error('确认失败：' + error.message)
  }
}

// 半自动模式：应用修改
const handleRevise = async (modifications) => {
  try {
    await reviewApi.confirm(currentTaskId.value, {
      action: 'revise',
      ...modifications,
    })
    showConfirmDialog.value = false
    ElMessage.success('修改已应用，继续执行...')
    // 恢复轮询
    startPolling(currentTaskId.value)
  } catch (error) {
    ElMessage.error('修改失败：' + error.message)
  }
}

// 半自动模式：终止任务
const handleAbort = async () => {
  try {
    await reviewApi.abort(currentTaskId.value)
    showConfirmDialog.value = false
    isRunning.value = false
    ElMessage.warning('任务已终止')
  } catch (error) {
    ElMessage.error('终止失败：' + error.message)
  }
}

onUnmounted(() => {
  if (pollInterval.value) {
    clearInterval(pollInterval.value)
  }
})
</script>

<style scoped>
.home-page {
  max-width: 1400px;
  margin: 0 auto;
}

.guide-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.el-timeline h4 {
  margin: 0 0 5px 0;
  font-size: 14px;
}

.el-timeline p {
  margin: 0;
  font-size: 12px;
  color: #909399;
}

/* 结果预览区域 - 全宽显示在底部 */
.result-section {
  margin-top: 30px;
  padding-top: 20px;
  border-top: 1px solid #e4e7ed;
}
</style>
