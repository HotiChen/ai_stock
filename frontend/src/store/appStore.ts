import { create } from 'zustand';
import type { User, AppMode } from '../types';

interface AppState {
  user: User | null;
  mode: AppMode;
  setUser: (user: User | null) => void;
  setMode: (mode: AppMode) => void;
}

export const useAppStore = create<AppState>()((set) => ({
  user: null,
  mode: 'simulation',
  setUser: (user) => set({ user }),
  setMode: (mode) => set({ mode }),
}));
