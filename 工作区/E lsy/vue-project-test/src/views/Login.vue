<template>
  <div class="login-container">
    <!-- 背景流光浮动球体 -->
    <div class="orb orb-1"></div>
    <div class="orb orb-2"></div>
    <div class="orb orb-3"></div>

    <!-- 登录框主体 -->
    <div class="login-box">
      <h2 class="title glow-text">{{ isLogin ? '园区安全智能调控平台' : '管理员注册' }}</h2>
      
      <form @submit.prevent="handleSubmit" class="form-content">
        <div class="form-group">
          <label>用户名</label>
          <input type="text" v-model="formData.username" placeholder="请输入管理员账号" required />
        </div>
        
        <div class="form-group">
          <label>密码</label>
          <input type="password" v-model="formData.password" placeholder="请输入密码" required />
        </div>
        
        <!-- 注册时才需要确认密码 -->
        <div class="form-group" v-if="!isLogin">
          <label>确认密码</label>
          <input type="password" v-model="formData.confirmPassword" placeholder="请再次输入密码" required />
        </div>

        <button type="submit" class="submit-btn">
          {{ isLogin ? '登 录' : '注 册' }}
        </button>
      </form>

      <div class="toggle-text" @click="isLogin = !isLogin">
        {{ isLogin ? '没有账号？点击注册 ➔' : '🔙 已有账号？返回登录' }}
      </div>

      <!-- 专属底部标识 -->
      <div class="footer-brand">
        designed by <span>QG</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'
import { loginAPI, registerAPI } from '../api/index'

const router = useRouter()
const userStore = useUserStore()

const isLogin = ref(true)

const formData = reactive({
  username: '',
  password: '',
  confirmPassword: ''
})

const handleSubmit = async () => {
  if (isLogin.value) {
    if (!formData.username || !formData.password) {
      alert('请输入账号和密码')
      return
    }
    try {
      const res: any = await loginAPI({
        username: formData.username,
        password: formData.password
      })
      if (res.code === 0 || res.code === 200) {
        userStore.setLoginState(res.data.token, formData.username)
        router.push('/dashboard')
      } else {
        alert('登录失败: ' + res.message)
      }
    } catch (error: any) {
      console.error('登录请求异常:', error)
      alert('无法连接到后端服务器，请确认后端已启动！')
    }
  } else {
    if (formData.password !== formData.confirmPassword) {
      alert('两次输入的密码不一致！')
      return
    }
    try {
      const res: any = await registerAPI({
        username: formData.username,
        password: formData.password
      })
      if (res.code === 0 || res.code === 200) {
        alert('注册成功，请登录')
        isLogin.value = true 
      } else {
        alert('注册失败: ' + res.message)
      }
    } catch (error) {
      alert('注册请求异常，请检查网络！')
    }
  }
}
</script>

<style scoped>
/* 1. 动态流光渐变背景 */
.login-container {
  height: 100vh;
  width: 100vw;
  display: flex;
  justify-content: center;
  align-items: center;
  position: relative;
  overflow: hidden;
  /* 深紫/暗蓝交织的流动渐变 */
  background: linear-gradient(-45deg, #090514, #1a0b2e, #2e0854, #0f172a);
  background-size: 400% 400%;
  animation: gradientFlow 15s ease infinite;
}

@keyframes gradientFlow {
  0% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}

/* 2. 悬浮发光球体 (增加空间呼吸感) */
.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  z-index: 0;
  animation: floatOrb 10s ease-in-out infinite alternate;
}
.orb-1 {
  width: 400px; height: 400px;
  background: rgba(147, 51, 234, 0.4); /* 紫色光晕 */
  top: -100px; left: -100px;
  animation-delay: 0s;
}
.orb-2 {
  width: 300px; height: 300px;
  background: rgba(56, 189, 248, 0.3); /* 霓虹蓝光晕 */
  bottom: -50px; right: -50px;
  animation-delay: -3s;
}
.orb-3 {
  width: 250px; height: 250px;
  background: rgba(168, 85, 247, 0.2);
  bottom: 20%; left: 20%;
  animation-duration: 12s;
}

@keyframes floatOrb {
  0% { transform: translateY(0px) scale(1); }
  100% { transform: translateY(40px) scale(1.1); }
}

/* 3. 毛玻璃登录框主体 */
.login-box {
  width: 420px;
  padding: 40px 50px;
  border-radius: 16px;
  position: relative;
  z-index: 10;
  /* 核心毛玻璃参数 */
  background: rgba(15, 10, 30, 0.55);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(168, 85, 247, 0.3);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.5), inset 0 0 0 1px rgba(255, 255, 255, 0.05);
}

/* 标题样式 */
.title {
  text-align: center;
  margin-bottom: 35px;
  font-size: 24px;
  letter-spacing: 2px;
}
.glow-text {
  color: #fff;
  text-shadow: 0 0 10px rgba(168, 85, 247, 0.8), 0 0 20px rgba(168, 85, 247, 0.4);
}

/* 表单元素样式 */
.form-group {
  margin-bottom: 24px;
}
label {
  display: block;
  margin-bottom: 8px;
  color: #c4b5fd; /* 浅紫色标签 */
  font-size: 13px;
  letter-spacing: 1px;
}
input {
  width: 100%;
  padding: 12px 15px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(168, 85, 247, 0.4);
  color: #fff;
  border-radius: 8px;
  box-sizing: border-box;
  font-size: 14px;
  transition: all 0.3s ease;
  outline: none;
}
input::placeholder {
  color: rgba(255, 255, 255, 0.3);
}
input:focus {
  background: rgba(255, 255, 255, 0.1);
  border-color: #a855f7;
  box-shadow: 0 0 12px rgba(168, 85, 247, 0.5);
}

/* 渐变按钮样式 */
.submit-btn {
  width: 100%;
  padding: 14px;
  background: linear-gradient(90deg, #7c3aed, #9333ea);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 16px;
  font-weight: bold;
  letter-spacing: 4px;
  margin-top: 15px;
  transition: all 0.3s ease;
  box-shadow: 0 4px 15px rgba(124, 58, 237, 0.4);
}
.submit-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(124, 58, 237, 0.6);
  background: linear-gradient(90deg, #8b5cf6, #a855f7);
}
.submit-btn:active {
  transform: translateY(1px);
}

/* 切换文本样式 */
.toggle-text {
  text-align: center;
  margin-top: 25px;
  color: #a78bfa;
  cursor: pointer;
  font-size: 13px;
  transition: color 0.3s;
}
.toggle-text:hover {
  color: #fff;
  text-shadow: 0 0 8px rgba(168, 85, 247, 0.8);
}

/* 4. 专属底部标识 */
.footer-brand {
  margin-top: 40px;
  text-align: center;
  color: rgba(255, 255, 255, 0.3);
  font-size: 12px;
  font-family: 'Courier New', Courier, monospace;
  letter-spacing: 1px;
}
.footer-brand span {
  color: #a855f7;
  font-weight: bold;
  font-size: 14px;
  text-shadow: 0 0 8px rgba(168, 85, 247, 0.5);
}
</style>