import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.golduzi.app',
  appName: 'GolduziApp',
  webDir: 'static',
  server: {
    androidScheme: 'http',
    cleartext: true,
    allowNavigation: ['185.222.163.15']
  },
  plugins: {
    CapacitorHttp: {
      enabled: true
    }
  }
};

export default config;
