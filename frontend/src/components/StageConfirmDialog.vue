<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    width="90%"
    top="5vh"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    class="stage-confirm-dialog"
  >
    <div class="dialog-content">
      <!-- 暂停原因说明 -->
      <el-alert
        :title="pauseMessage"
        type="info"
        :closable="false"
        show-icon
        class="pause-alert"
      />

      <!-- 规划阶段审核 -->
      <div v-if="pauseReason === 'after_planning'" class="stage-review">
        <div class="review-section">
          <h3><el-icon><Document /></el-icon> 主题分析结果</h3>
          <div class="info-grid">
            <div class="info-item">
              <span class="label">研究领域：</span>
              <el-tag>{{ topicAnalysis?.domain || '未设置' }}</el-tag>
            </div>
            <div class="info-item">
              <span class="label">子领域：</span>
              <el-tag v-for="sub in topicAnalysis?.sub_domains" :key="sub" type="info" size="small">{{ sub }}</el-tag>
            </div>
          </div>
          <div class="info-item full-width">
            <span class="label">提取的关键词：</span>
            <div class="keywords-container">
              <el-tag
                v-for="kw in topicAnalysis?.keywords"
                :key="kw"
                type="success"
                closable
                @close="removeKeyword(kw)"
              >{{ kw }}</el-tag>
              <el-input
                v-model="newKeyword"
                placeholder="添加关键词"
                size="small"
                style="width: 120px"
                @keyup.enter="addKeyword"
              />
              <el-button size="small" @click="addKeyword">添加</el-button>
            </div>
          </div>
        </div>

        <div class="review-section">
          <h3><el-icon><Search /></el-icon> 检索策略</h3>
          <div class="search-terms-list">
            <div v-for="(term, index) in searchTerms" :key="index" class="search-term-item">
              <el-input v-model="searchTerms[index]" placeholder="检索词" />
              <el-button type="danger" size="small" @click="removeSearchTerm(index)" :icon="Delete" circle />
            </div>
            <el-button type="primary" size="small" @click="addSearchTerm" :icon="Plus">添加检索词</el-button>
          </div>
        </div>

        <div class="review-section">
          <h3><el-icon><Reading /></el-icon> 章节结构</h3>
          <el-table :data="planSections" size="small" stripe max-height="300">
            <el-table-column prop="title" label="章节标题" min-width="150" />
            <el-table-column prop="target_words" label="目标字数" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" type="info">{{ row.target_words }} 字</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="goal" label="写作目标" min-width="200" show-overflow-tooltip />
          </el-table>
        </div>
      </div>

      <!-- 筛选阶段审核 -->
      <div v-if="pauseReason === 'after_screening'" class="stage-review">
        <div class="review-section">
          <h3><el-icon><Files /></el-icon> 文献筛选结果</h3>
          <div class="stats-row">
            <el-statistic title="检索文献" :value="totalRetrieved" />
            <el-statistic title="选中文献" :value="selectedPapers.length" />
            <el-statistic title="目标文献" :value="targetRefs" />
          </div>
          <!-- 文献不足警告 -->
          <el-alert
            v-if="selectedPapers.length < targetRefs"
            :title="`选中文献数量（${selectedPapers.length}）低于目标数量（${targetRefs}）`"
            type="warning"
            :closable="false"
            show-icon
            style="margin-top: 15px"
          >
            <template #default>
              建议检查检索关键词或从候选文献中添加更多文献。如仍无法达到目标，可以点击"确认继续"，系统将使用当前文献继续生成。
            </template>
          </el-alert>
        </div>

        <div class="review-section">
          <h3><el-icon><Select /></el-icon> 已选中的文献（点击可移除）</h3>
          <div class="papers-list">
            <el-table :data="selectedPapers" size="small" stripe max-height="400" @row-click="togglePaperSelection">
              <el-table-column width="50">
                <template #default="{ row }">
                  <el-checkbox v-model="row._selected" @click.stop />
                </template>
              </el-table-column>
              <el-table-column prop="ref_id" label="ID" width="80" />
              <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
              <el-table-column prop="year" label="年份" width="80" align="center" />
              <el-table-column prop="journal" label="期刊" width="150" show-overflow-tooltip />
              <el-table-column prop="jcr_quartile" label="分区" width="80" align="center">
                <template #default="{ row }">
                  <el-tag :type="getQuartileType(row.jcr_quartile)" size="small">{{ row.jcr_quartile }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="relevance_score" label="相关性" width="80" align="center">
                <template #default="{ row }">
                  <el-progress :percentage="row.relevance_score * 10" :stroke-width="6" />
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <div class="review-section">
          <h3><el-icon><FolderOpened /></el-icon> 候选文献（可添加）</h3>
          <el-collapse accordion>
            <el-collapse-item
              v-for="paper in candidatePapers"
              :key="paper.ref_id"
              :name="paper.ref_id"
            >
              <template #title>
                <div class="candidate-paper-title">
                  <el-button
                    type="success"
                    size="small"
                    :icon="Plus"
                    @click.stop="addPaper(paper)"
                  >添加</el-button>
                  <span>{{ paper.title }}</span>
                  <el-tag size="small">{{ paper.year }}</el-tag>
                </div>
              </template>
              <div class="paper-detail">
                <p><strong>期刊：</strong>{{ paper.journal }}</p>
                <p><strong>摘要：</strong>{{ paper.abstract_preview }}</p>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>

      <!-- 与 AI 对话 -->
      <div class="chat-section">
        <h3><el-icon><ChatDotRound /></el-icon> 与 AI 讨论</h3>
        <div class="chat-container">
          <div class="chat-messages" ref="chatMessagesRef">
            <div v-for="(msg, index) in chatMessages" :key="index" :class="['chat-message', msg.role]">
              <div class="message-content">{{ msg.content }}</div>
            </div>
          </div>
          <div class="chat-input">
            <el-input
              v-model="chatInput"
              placeholder="输入问题或修改建议..."
              @keyup.enter="sendChatMessage"
            />
            <el-button type="primary" @click="sendChatMessage" :loading="chatLoading">发送</el-button>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleAbort" type="danger">终止任务</el-button>
        <el-button @click="handleRevise" type="warning">应用修改</el-button>
        <el-button @click="handleConfirm" type="success" :disabled="!canConfirm">确认继续</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'

const props = defineProps({
  show: {
    type: Boolean,
    default: false,
  },
  pauseReason: {
    type: String,
    default: '',
  },
  topicAnalysis: {
    type: Object,
    default: null,
  },
  planSections: {
    type: Array,
    default: () => [],
  },
  selectedPapers: {
    type: Array,
    default: () => [],
  },
  candidatePapers: {
    type: Array,
    default: () => [],
  },
  totalRetrieved: {
    type: Number,
    default: 0,
  },
  targetRefs: {
    type: Number,
    default: 40,
  },
  taskId: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['update:show', 'confirm', 'revise', 'abort', 'chat'])

const visible = computed({
  get: () => props.show,
  set: (val) => emit('update:show', val),
})

const dialogTitle = computed(() => {
  const titles = {
    'after_planning': '审核检索策略',
    'after_screening': '审核筛选文献',
  }
  return titles[props.pauseReason] || '审核确认'
})

const pauseMessage = computed(() => {
  const messages = {
    'after_planning': '规划阶段已完成。请审核检索策略和章节结构，确认后将继续文献检索。',
    'after_screening': '文献筛选已完成。请审核选中的文献，可以添加或移除文献，确认后将继续分析。',
  }
  return messages[props.pauseReason] || '请审核当前阶段结果。'
})

// 规划阶段数据
const localKeywords = ref([])
const newKeyword = ref('')
const searchTerms = ref([])

// 筛选阶段数据
const localSelectedPapers = ref([])

// 聊天相关
const chatMessages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
const chatMessagesRef = ref(null)

// 监听 props 变化，初始化本地数据
watch(() => props.show, (val) => {
  if (val) {
    // 初始化规划阶段数据
    if (props.topicAnalysis?.keywords) {
      localKeywords.value = [...props.topicAnalysis.keywords]
    }
    if (props.topicAnalysis?.search_terms) {
      searchTerms.value = [...props.topicAnalysis.search_terms]
    }

    // 初始化筛选阶段数据
    if (props.selectedPapers) {
      localSelectedPapers.value = props.selectedPapers.map(p => ({
        ...p,
        _selected: true,
      }))
    }

    // 清空聊天记录
    chatMessages.value = []
  }
})

// 关键词操作
const removeKeyword = (kw) => {
  const index = localKeywords.value.indexOf(kw)
  if (index > -1) {
    localKeywords.value.splice(index, 1)
  }
}

const addKeyword = () => {
  if (newKeyword.value && !localKeywords.value.includes(newKeyword.value)) {
    localKeywords.value.push(newKeyword.value)
    newKeyword.value = ''
  }
}

// 检索词操作
const addSearchTerm = () => {
  searchTerms.value.push('')
}

const removeSearchTerm = (index) => {
  searchTerms.value.splice(index, 1)
}

// 文献操作
const togglePaperSelection = (row) => {
  row._selected = !row._selected
}

const addPaper = (paper) => {
  if (!localSelectedPapers.value.find(p => p.ref_id === paper.ref_id)) {
    localSelectedPapers.value.push({
      ...paper,
      _selected: true,
    })
    ElMessage.success(`已添加文献 ${paper.ref_id}`)
  }
}

// 聊天功能
const sendChatMessage = async () => {
  if (!chatInput.value.trim()) return

  const userMessage = chatInput.value.trim()
  chatMessages.value.push({ role: 'user', content: userMessage })
  chatInput.value = ''

  chatLoading.value = true
  try {
    const response = await fetch(`/api/v1/reviews/${props.taskId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: userMessage }),
    })

    if (response.ok) {
      const data = await response.json()
      chatMessages.value.push({ role: 'assistant', content: data.response })

      // 如果有建议修改，显示
      if (data.suggested_modifications && Object.keys(data.suggested_modifications).length > 0) {
        // 可以自动应用到本地数据
        if (data.suggested_modifications.suggested_keywords) {
          localKeywords.value = data.suggested_modifications.suggested_keywords
        }
      }
    } else {
      chatMessages.value.push({ role: 'assistant', content: '抱歉，AI 服务暂时不可用。' })
    }
  } catch (error) {
    chatMessages.value.push({ role: 'assistant', content: `发生错误：${error.message}` })
  } finally {
    chatLoading.value = false
    // 滚动到底部
    nextTick(() => {
      if (chatMessagesRef.value) {
        chatMessagesRef.value.scrollTop = chatMessagesRef.value.scrollHeight
      }
    })
  }
}

// 操作按钮
const canConfirm = computed(() => {
  if (props.pauseReason === 'after_screening') {
    // 至少要有一些文献才能确认，但如果确实没有检索到文献，允许用户选择继续
    const selectedCount = localSelectedPapers.value.filter(p => p._selected).length
    return selectedCount >= 5 || (selectedCount > 0 && props.selectedPapers?.length <= 10)
  }
  return true
})

const handleConfirm = () => {
  emit('confirm')
}

const handleRevise = () => {
  const modifications = {}

  if (props.pauseReason === 'after_planning') {
    modifications.updated_keywords = localKeywords.value
    modifications.updated_search_terms = searchTerms.value.filter(t => t.trim())
  } else if (props.pauseReason === 'after_screening') {
    modifications.removed_paper_ids = localSelectedPapers.value
      .filter(p => !p._selected)
      .map(p => p.ref_id)
    modifications.added_paper_ids = localSelectedPapers.value
      .filter(p => p._selected && !props.selectedPapers.find(sp => sp.ref_id === p.ref_id))
      .map(p => p.ref_id)
  }

  emit('revise', modifications)
}

const handleAbort = () => {
  emit('abort')
}

// 工具函数
const getQuartileType = (quartile) => {
  const types = { 'Q1': 'success', 'Q2': 'primary', 'Q3': 'warning', 'Q4': 'info' }
  return types[quartile] || ''
}
</script>

<style scoped>
.stage-confirm-dialog :deep(.el-dialog__body) {
  max-height: 70vh;
  overflow-y: auto;
}

.pause-alert {
  margin-bottom: 20px;
}

.stage-review {
  margin-bottom: 30px;
}

.review-section {
  margin-bottom: 25px;
  padding: 15px;
  background: #f5f7fa;
  border-radius: 8px;
}

.review-section h3 {
  margin: 0 0 15px 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #303133;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 15px;
  margin-bottom: 15px;
}

.info-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.info-item.full-width {
  grid-column: 1 / -1;
  flex-direction: column;
}

.info-item .label {
  color: #606266;
  font-size: 13px;
  min-width: 80px;
  padding-top: 5px;
}

.keywords-container {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.search-terms-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.search-term-item {
  display: flex;
  gap: 10px;
  align-items: center;
}

.stats-row {
  display: flex;
  gap: 40px;
  justify-content: center;
  margin: 20px 0;
}

.papers-list {
  max-height: 400px;
  overflow-y: auto;
}

.candidate-paper-title {
  display: flex;
  align-items: center;
  gap: 15px;
  width: 100%;
}

.candidate-paper-title span {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.paper-detail {
  padding: 10px;
  background: #fff;
  border-radius: 4px;
  font-size: 13px;
}

.paper-detail p {
  margin: 5px 0;
}

.chat-section {
  margin-top: 25px;
  padding: 15px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}

.chat-section h3 {
  margin: 0 0 15px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-container {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.chat-messages {
  height: 200px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  padding: 15px;
  background: #fafafa;
}

.chat-message {
  margin-bottom: 15px;
}

.chat-message.user {
  text-align: right;
}

.chat-message.user .message-content {
  background: #409eff;
  color: #fff;
}

.chat-message.assistant .message-content {
  background: #f0f0f0;
  color: #303133;
}

.message-content {
  display: inline-block;
  padding: 10px 15px;
  border-radius: 8px;
  max-width: 80%;
  text-align: left;
  white-space: pre-wrap;
}

.chat-input {
  display: flex;
  gap: 10px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 15px;
}
</style>
