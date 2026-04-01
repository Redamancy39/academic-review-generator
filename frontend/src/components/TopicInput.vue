<template>
  <el-card class="topic-input-card" shadow="hover">
    <template #header>
      <div class="card-header">
        <el-icon><Edit /></el-icon>
        <span>输入研究主题</span>
      </div>
    </template>

    <el-form
      ref="formRef"
      :model="formData"
      :rules="rules"
      label-position="top"
    >
      <el-form-item label="研究主题" prop="topic">
        <el-input
          v-model="formData.topic"
          type="textarea"
          :rows="2"
          placeholder="请输入研究主题，例如：深度学习在医学影像诊断中的应用综述"
          maxlength="200"
          show-word-limit
        />
      </el-form-item>

      <el-form-item label="写作期望（可选）">
        <el-input
          v-model="formData.user_description"
          type="textarea"
          :rows="4"
          placeholder="描述您对该综述的理解、希望重点讨论的内容、写作风格偏好等。&#10;例如：&#10;- 希望重点关注XX领域的应用案例&#10;- 需要对比分析A方法和B方法的优劣&#10;- 希望采用综述+批判性分析的写作风格"
          maxlength="1000"
          show-word-limit
        />
        <div class="form-tip">
          <el-icon><InfoFilled /></el-icon>
          <span>详细描述您的想法可以让生成的综述更符合您的期望</span>
        </div>
      </el-form-item>

      <!-- 期刊类型和语言选择 -->
      <el-row :gutter="20">
        <el-col :span="12">
          <el-form-item label="目标期刊类型">
            <el-select v-model="formData.journal_type" placeholder="选择期刊类型">
              <el-option label="中文核心期刊" value="中文核心期刊" />
              <el-option label="中文顶级期刊" value="中文顶级期刊" />
              <el-option label="SCI期刊" value="SCI期刊" />
              <el-option label="EI期刊" value="EI期刊" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="综述语言">
            <el-select v-model="formData.language" placeholder="选择语言">
              <el-option label="中文" value="中文" />
              <el-option label="英文" value="英文" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <el-collapse v-model="activeCollapse">
        <el-collapse-item title="高级设置" name="advanced">
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="字数范围">
                <el-slider
                  v-model="wordCountRange"
                  range
                  :min="2000"
                  :max="15000"
                  :step="500"
                  show-stops
                  :format-tooltip="formatWordCount"
                />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="目标参考文献数">
                <el-input-number
                  v-model="formData.target_refs"
                  :min="20"
                  :max="150"
                  :step="5"
                />
              </el-form-item>
            </el-col>
          </el-row>

          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="检索池大小">
                <el-input-number
                  v-model="formData.retrieval_pool_size"
                  :min="50"
                  :max="300"
                  :step="10"
                />
                <div class="form-tip">
                  <span>建议设为目标文献数的 2-3 倍</span>
                </div>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="年份窗口">
                <el-select v-model="formData.year_window">
                  <el-option :value="3" label="近3年" />
                  <el-option :value="5" label="近5年" />
                  <el-option :value="7" label="近7年" />
                  <el-option :value="10" label="近10年" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>

          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="审稿轮次">
                <el-slider
                  v-model="reviewRoundsRange"
                  range
                  :min="1"
                  :max="5"
                  show-stops
                />
              </el-form-item>
            </el-col>
          </el-row>
        </el-collapse-item>
      </el-collapse>

      <el-form-item class="submit-btn">
        <el-button
          type="primary"
          size="large"
          :loading="isLoading"
          :disabled="isRunning"
          @click="handleSubmit"
        >
          <el-icon><Position /></el-icon>
          {{ isRunning ? '任务执行中...' : '开始生成综述' }}
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  isLoading: {
    type: Boolean,
    default: false,
  },
  isRunning: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['submit'])

const formRef = ref(null)
const activeCollapse = ref([])

const formData = reactive({
  topic: '',
  user_description: '',
  journal_type: '中文核心期刊',
  language: '中文',
  word_count_min: 4000,
  word_count_max: 6000,
  target_refs: 40,
  retrieval_pool_size: 100,
  year_window: 5,
  review_rounds_min: 2,
  review_rounds_max: 3,
})

const wordCountRange = computed({
  get: () => [formData.word_count_min, formData.word_count_max],
  set: (val) => {
    formData.word_count_min = val[0]
    formData.word_count_max = val[1]
  },
})

const reviewRoundsRange = computed({
  get: () => [formData.review_rounds_min, formData.review_rounds_max],
  set: (val) => {
    formData.review_rounds_min = val[0]
    formData.review_rounds_max = val[1]
  },
})

const rules = {
  topic: [
    { required: true, message: '请输入研究主题', trigger: 'blur' },
    { min: 10, message: '主题至少需要10个字符', trigger: 'blur' },
  ],
}

const formatWordCount = (val) => `${val} 字`

const handleSubmit = async () => {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
    emit('submit', { ...formData })
  } catch {
    ElMessage.warning('请正确填写表单')
  }
}
</script>

<style scoped>
.topic-input-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.form-tip {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
}

.submit-btn {
  margin-top: 20px;
  text-align: center;
}

.submit-btn .el-button {
  width: 100%;
  max-width: 300px;
}
</style>
