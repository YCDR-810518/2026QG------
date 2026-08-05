<template>
  <div id="app-container">
    <!-- 路由匹配到的组件（Dashboard、Admin、Login）将渲染在这里 -->
    <router-view />
  </div>
</template>

<script setup lang="ts">
// 全局入口组件
</script>

<style>
/* 清除默认边距，大屏全屏显示 */
html, body, #app, #app-container {
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0;
  overflow: hidden;
}
</style>