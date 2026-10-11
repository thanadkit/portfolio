/** ต้องตรงกับ tailwind.config ที่ฝังอยู่ใน public/index.html (ตัวที่ Play CDN ใช้)
 *  ถ้าแก้ที่นั่นต้องแก้ที่นี่ด้วย ไม่งั้นสีจะเพี้ยนเฉพาะเวอร์ชัน build */
module.exports = {
  // สแกนหา class จาก index.html รวมถึง class ที่อยู่ใน template literal ของ JavaScript
  content: ['./public/index.html'],
  theme: {
    extend: {
      colors: {
        gold:    { DEFAULT: '#d4af37', light: '#fde047', dark: '#997a00' },
        surface: { 100: '#111111', 200: '#161616', 300: '#262626' },
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['Roboto Mono', 'monospace'],
      },
    },
  },
  plugins: [],
};
