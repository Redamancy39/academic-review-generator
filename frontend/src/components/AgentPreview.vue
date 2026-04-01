<template>
  <el-card class="agent-preview-card" shadow="hover">
    <template #header>
      <div class="card-header">
        <el-icon><User /></el-icon>
        <span>Agent 预览</span>
      </div>
    </template>

    <el-collapse v-if="agents">
      <el-collapse-item
        v-for="(agent, key) in agents"
        :key="key"
        :name="key"
      >
        <template #title>
          <div class="agent-title">
            <el-tag :type="getAgentTagType(key)" size="small">
              {{ getAgentLabel(key) }}
            </el-tag>
            <span class="agent-role">{{ agent.role }}</span>
          </div>
        </template>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="角色">
            {{ agent.role }}
          </el-descriptions-item>
          <el-descriptions-item label="目标">
            <div class="multi-line">{{ agent.goal }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="背景">
            <div class="multi-line">{{ agent.backstory }}</div>
          </el-descriptions-item>
        </el-descriptions>
      </el-collapse-item>
    </el-collapse>

    <el-empty v-else description="尚未生成 Agent 定义" />
  </el-card>
</template>

<script setup>
defineProps({
  agents: {
    type: Object,
    default: null,
  },
})

const agentLabels = {
  planner: '规划者',
  retriever: '检索者',
  screener: '筛选者',
  analyzer: '分析者',
  writer: '撰写者',
  reviewer: '审稿者',
}

const agentTagTypes = {
  planner: 'primary',
  retriever: 'success',
  screener: 'warning',
  analyzer: 'info',
  writer: 'danger',
  reviewer: '',
}

const getAgentLabel = (key) => agentLabels[key] || key
const getAgentTagType = (key) => agentTagTypes[key] || ''
</script>

<style scoped>
.agent-preview-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.agent-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.agent-role {
  font-weight: 500;
}

.multi-line {
  white-space: pre-wrap;
  line-height: 1.6;
}
</style>
