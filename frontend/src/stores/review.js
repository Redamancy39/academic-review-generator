// Pinia store for review state management
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { reviewApi, topicApi, agentApi } from '@/api'

export const useReviewStore = defineStore('review', () => {
  // State
  const currentTask = ref(null)
  const taskList = ref([])
  const topicAnalysis = ref(null)
  const agentDefinitions = ref(null)
  const isLoading = ref(false)
  const error = ref(null)

  // Topic Analysis
  async function analyzeTopic(topic) {
    isLoading.value = true
    error.value = null
    try {
      const result = await topicApi.analyze(topic)
      topicAnalysis.value = result
      return result
    } catch (e) {
      error.value = e.message || '主题分析失败'
      throw e
    } finally {
      isLoading.value = false
    }
  }

  // Agent Generation
  async function generateAgents(data) {
    isLoading.value = true
    error.value = null
    try {
      const result = await agentApi.generate(data)
      agentDefinitions.value = result
      return result
    } catch (e) {
      error.value = e.message || 'Agent生成失败'
      throw e
    } finally {
      isLoading.value = false
    }
  }

  // Create Review Task
  async function createReview(data) {
    isLoading.value = true
    error.value = null
    try {
      const result = await reviewApi.create(data)
      currentTask.value = result
      return result
    } catch (e) {
      error.value = e.message || '创建任务失败'
      throw e
    } finally {
      isLoading.value = false
    }
  }

  // Get Task Status
  async function getTaskStatus(taskId) {
    try {
      const result = await reviewApi.get(taskId)
      currentTask.value = result
      return result
    } catch (e) {
      error.value = e.message || '获取任务状态失败'
      throw e
    }
  }

  // Get Task Result
  async function getTaskResult(taskId) {
    try {
      const result = await reviewApi.getResult(taskId)
      return result
    } catch (e) {
      error.value = e.message || '获取任务结果失败'
      throw e
    }
  }

  // List Tasks
  async function listTasks() {
    try {
      const result = await reviewApi.list()
      taskList.value = result
      return result
    } catch (e) {
      error.value = e.message || '获取任务列表失败'
      throw e
    }
  }

  // Reset State
  function reset() {
    currentTask.value = null
    topicAnalysis.value = null
    agentDefinitions.value = null
    error.value = null
  }

  return {
    // State
    currentTask,
    taskList,
    topicAnalysis,
    agentDefinitions,
    isLoading,
    error,
    // Actions
    analyzeTopic,
    generateAgents,
    createReview,
    getTaskStatus,
    getTaskResult,
    listTasks,
    reset,
  }
})
