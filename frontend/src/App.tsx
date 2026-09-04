import { useState } from 'react';
import Login from './pages/Login';
import Chat from './pages/Chat';

function App() {
  // 【核心修复】初始化时直接从 sessionStorage 读取用户信息，实现刷新保持
  const [user, setUser] = useState<any>(() => {
    try {
      const savedUser = sessionStorage.getItem('userData');
      return savedUser ? JSON.parse(savedUser) : null;
    } catch {
      return null;
    }
  });

  // 登录成功后保存用户信息
  const handleLogin = (userData: any) => {
    sessionStorage.setItem('userData', JSON.stringify(userData));
    sessionStorage.setItem('suo_user', userData.username);
    setUser(userData);
  };

  // 退出登录时清除用户信息
  const handleLogout = () => {
    sessionStorage.removeItem('userData');
    sessionStorage.removeItem('suo_user');
    setUser(null);
  };

  return (
    <div className="App">
      {!user ? (
        <Login onLogin={handleLogin} />
      ) : (
        <Chat user={user} onLogout={handleLogout} />
      )}
    </div>
  );
}

export default App;