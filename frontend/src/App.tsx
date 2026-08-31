import { useState } from 'react';
import Login from './pages/Login';
import Chat from './pages/Chat';

function App() {
  const [user, setUser] = useState(null);

  return (
    <div className="App">
      {!user ? (
        <Login onLogin={setUser} />
      ) : (
        <Chat user={user} />
      )}
    </div>
  );
}

export default App;