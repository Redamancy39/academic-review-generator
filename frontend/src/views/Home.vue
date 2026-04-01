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
              <h4>生成综述</h4>
              <p>系统将自动进行文献检索、筛选、分析和撰写</p>
            </el-timeline-item>
            <el-timeline-item>
              <h4>审稿修订</h4>
              <p>多轮自动审稿确保综述质量</p>
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
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useReviewStore } from '@/stores/review'
import TopicInput from '@/components/TopicInput.vue'
import ParameterConfig from '@/components/ParameterConfig.vue'
import ProgressMonitor from '@/components/ProgressMonitor.vue'
import ResultPreview from '@/components/ResultPreview.vue'
import AgentPreview from '@/components/AgentPreview.vue'

const router = useRouter()
const store = useReviewStore()

const currentConfig = ref(null)
const result = ref(null)
const pollInterval = ref(null)
const isRunning = ref(false) // 防止重复点击的状态
const currentTaskId = ref('') // 当前任务ID，用于导出文献

const showConfig = computed(() => currentConfig.value !== null)

const handleSubmit = async (formData) => {
  // 防止重复点击
  if (isRunning.value) {
    ElMessage.warning('任务正在执行中，请等待完成')
    return
  }

  try {
    isRunning.value = true
    currentConfig.value = formData

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

    // 创建任务
    ElMessage.info('正在创建任务...')
    const task = await store.createReview(formData)

    currentTaskId.value = task.task_id
    ElMessage.success('任务创建成功，开始执行...')

    // 开始轮询任务状态
    startPolling(task.task_id)
  } catch (error) {
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

      if (status.status === 'completed') {
        clearInterval(pollInterval.value)
        pollInterval.value = null
        isRunning.value = false

        // 获取结果
        result.value = await store.getTaskResult(taskId)
        ElMessage.success('综述生成完成！')
      } else if (status.status === 'failed') {
        clearInterval(pollInterval.value)
        pollInterval.value = null
        isRunning.value = false
        ElMessage.error('任务执行失败')
      }
    } catch (error) {
      console.error('轮询失败:', error)
    }
  }, 2000)
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
