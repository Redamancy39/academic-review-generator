<template>
  <div class="task-detail-page">
    <el-page-header @back="$router.push('/')">
      <template #content>
        <span class="page-title">任务详情</span>
      </template>
    </el-page-header>

    <el-divider />

    <el-row :gutter="20" v-if="task">
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header>
            <div class="card-header">
              <el-icon><InfoFilled /></el-icon>
              <span>任务信息</span>
            </div>
          </template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="任务ID">{{ task.task_id }}</el-descriptions-item>
            <el-descriptions-item label="主题">{{ task.topic }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="getStatusType(task.status)">{{ getStatusLabel(task.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ task.created_at }}</el-descriptions-item>
            <el-descriptions-item label="进度">{{ Math.round(task.progress * 100) }}%</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <el-col :span="12">
        <ProgressMonitor
          :stage="task.current_stage"
          :progress="task.progress"
          :message="task.message"
          :status="task.status"
        />
      </el-col>
    </el-row>

    <el-row :gutter="20" v-if="result" style="margin-top: 20px;">
      <el-col :span="24">
        <ResultPreview
          :markdown="result.final_markdown"
          :validation="result.validation"
        />
      </el-col>
    </el-row>

    <el-empty v-if="!task" description="任务不存在" />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useReviewStore } from '@/stores/review'
import ProgressMonitor from '@/components/ProgressMonitor.vue'
import ResultPreview from '@/components/ResultPreview.vue'

const route = useRoute()
const store = useReviewStore()

const task = ref(null)
const result = ref(null)
const pollInterval = ref(null)

const statusLabels = {
  pending: '等待中',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
}

const statusTypes = {
  pending: 'info',
  running: 'warning',
  completed: 'success',
  failed: 'danger',
}

const getStatusLabel = (status) => statusLabels[status] || status
const getStatusType = (status) => statusTypes[status] || ''

const loadTask = async () => {
  const taskId = route.params.id
  if (!taskId) return

  try {
    task.value = await store.getTaskStatus(taskId)

    if (task.value.status === 'completed') {
      result.value = await store.getTaskResult(taskId)
    } else if (task.value.status === 'running') {
      startPolling(taskId)
    }
  } catch (error) {
    console.error('加载任务失败:', error)
  }
}

const startPolling = (taskId) => {
  if (pollInterval.value) {
    clearInterval(pollInterval.value)
  }

  pollInterval.value = setInterval(async () => {
    try {
      const status = await store.getTaskStatus(taskId)
      task.value = status

      if (status.status === 'completed') {
        clearInterval(pollInterval.value)
        pollInterval.value = null
        result.value = await store.getTaskResult(taskId)
      } else if (status.status === 'failed') {
        clearInterval(pollInterval.value)
        pollInterval.value = null
      }
    } catch (error) {
      console.error('轮询失败:', error)
    }
  }, 2000)
}

onMounted(() => {
  loadTask()
})

onUnmounted(() => {
  if (pollInterval.value) {
    clearInterval(pollInterval.value)
  }
})
</script>

<style scoped>
.task-detail-page {
  max-width: 1200px;
  margin: 0 auto;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
</style>
