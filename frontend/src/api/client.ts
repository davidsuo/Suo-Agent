import axios from 'axios';

// 【本地调试方案】直接指向后端服务，利用后端配置的 CORS 跨域
// 上线到 Render 时，我们需要改为 '/api' 并使用 FastAPI 静态托管
const api = axios.create({
  baseURL: 'http://localhost:10000/api', // 直接跨域请求后端
  timeout: 120000,
});

export default api;