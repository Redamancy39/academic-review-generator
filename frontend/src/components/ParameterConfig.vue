<template>
  <el-card class="param-config-card" shadow="hover">
    <template #header>
      <div class="card-header">
        <el-icon><Setting /></el-icon>
        <span>参数配置</span>
      </div>
    </template>

    <el-descriptions :column="2" border>
      <el-descriptions-item label="研究主题" :span="2">
        {{ config.topic }}
      </el-descriptions-item>
      <el-descriptions-item label="写作期望" :span="2" v-if="config.user_description">
        <div class="user-description">{{ config.user_description }}</div>
      </el-descriptions-item>
      <el-descriptions-item label="期刊类型">
        {{ config.journal_type || '中文核心期刊' }}
      </el-descriptions-item>
      <el-descriptions-item label="综述语言">
        {{ config.language || '中文' }}
      </el-descriptions-item>
      <el-descriptions-item label="目标字数">
        {{ config.word_count_min }} - {{ config.word_count_max }} 字
      </el-descriptions-item>
      <el-descriptions-item label="目标参考文献">
        {{ config.target_refs }} 篇
      </el-descriptions-item>
      <el-descriptions-item label="检索池大小">
        {{ config.retrieval_pool_size || 100 }} 篇
      </el-descriptions-item>
      <el-descriptions-item label="年份窗口">
        近 {{ config.year_window }} 年
      </el-descriptions-item>
      <el-descriptions-item label="审稿轮次">
        {{ config.review_rounds_min }} - {{ config.review_rounds_max }} 轮
      </el-descriptions-item>
    </el-descriptions>
  </el-card>
</template>

<script setup>
defineProps({
  config: {
    type: Object,
    required: true,
  },
})
</script>

<style scoped>
.param-config-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.user-description {
  white-space: pre-wrap;
  line-height: 1.6;
  color: #606266;
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 4px;
  max-height: 150px;
  overflow-y: auto;
}
</style>
