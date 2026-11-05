import { createRoot } from 'react-dom/client';
import { captureTokenFromHash } from './api.js';
import App from './App.jsx';
import './styles.css';

captureTokenFromHash(); // back from /auth/callback with #id_token=…
createRoot(document.getElementById('root')).render(<App />);
