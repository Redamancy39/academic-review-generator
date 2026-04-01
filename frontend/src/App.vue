<template>
  <div id="app">
    <el-container class="app-container">
      <el-header class="app-header">
        <div class="header-content">
          <h1 class="app-title">
            <el-icon><Document /></el-icon>
            学术综述自动生成系统
          </h1>
          <div class="header-nav">
            <el-button text @click="$router.push('/')">
              <el-icon><HomeFilled /></el-icon>
              首页
            </el-button>
          </div>
        </div>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
      <el-footer class="app-footer">
        <span>学术综述自动生成系统 v1.0.0</span>
      </el-footer>
    </el-container>
  </div>
</template>

<script setup>
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #app {
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.app-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 0 20px;
  height: 60px;
  display: flex;
  align-items: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.app-title {
  font-size: 20px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-nav .el-button {
  color: white;
}

.app-main {
  flex: 1;
  background-color: #f5f7fa;
  padding: 20px;
}

.app-footer {
  background-color: #303133;
  color: #909399;
  text-align: center;
  padding: 12px 0;
  font-size: 12px;
}
</style>
