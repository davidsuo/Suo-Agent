import axios from 'axios';

// 【本地调试方案】直接指向后端服务，利用后端配置的 CORS 跨域
// 上线到 Render 时，我们需要改为 '/api' 并使用 FastAPI 静态托管
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL, // 动态读取环境变量
  timeout: 120000,
});

export default api;