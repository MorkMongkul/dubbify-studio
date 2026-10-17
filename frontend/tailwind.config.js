/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-sans)', 'Inter', 'system-ui', 'sans-serif'],
        display: ['var(--font-display)', 'Sora', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        // Movelo Studio coral (brand kit: coral #F0506E, Studio gradient
        // #FF6A5C → #E2366E). DEFAULT is a deeper step so white button text
        // stays readable (4.7:1).
        brand: {
          DEFAULT: '#D6305F',
          50:  '#FFF0F3',
          100: '#FDE7EE',
          200: '#FBCAD6',
          300: '#F79AB0',
          400: '#F4728D',
          500: '#F0506E',
          600: '#D6305F',
          700: '#B8284F',
          800: '#9E2640',
          900: '#7A1E36',
          hover: '#E2366E',
          subtle: 'rgba(240,80,110,0.15)',
          glow:  'rgba(240,80,110,0.4)',
        },
        // Movelo gold — the second accent.
        accent: {
          DEFAULT: '#F5B841',
          hover: '#FFC95E',
          subtle: 'rgba(245,184,65,0.15)',
        },
        studio: {
          from: '#FF6A5C',
          to:   '#E2366E',
        },
        // Movelo's navy greys (midnight #0B1230, navy #141C45, mist #AEB4D6,
        // slate #5A6185) in place of a neutral grey.
        ink: {
          50:  '#F7F7FB',
          100: '#EEEFF6',
          200: '#E2E4EF',
          300: '#CDD1E3',
          400: '#AEB4D6',
          500: '#7F87B0',
          600: '#5A6185',
          700: '#2A3470',
          800: '#1A2252',
          900: '#0B1230',
          950: '#080D22',
        },
        surface: {
          0: 'var(--color-surface-0)',
          1: 'var(--color-surface-1)',
          2: 'var(--color-surface-2)',
          3: 'var(--color-surface-3)',
          4: 'var(--color-surface-4)',
          5: 'var(--color-surface-5)',
          6: 'var(--color-surface-5)',
        },
        border: {
          subtle:  'var(--color-border-subtle)',
          DEFAULT: 'var(--color-border)',
          strong:  'var(--color-border-strong)',
          brand:   'rgba(240,80,110,0.4)',
        },
        text: {
          primary:   'var(--color-text-primary)',
          secondary: 'var(--color-text-secondary)',
          muted:     'var(--color-text-muted)',
          disabled:  'var(--color-text-disabled)',
        },
        status: {
          success: '#10B981',
          warning: '#F59E0B',
          error:   '#EF4444',
          info:    '#3B82F6',
          pending: '#F0506E',
        },
        // Speaker track colors
        speaker: {
          0: '#2563EB',
          1: '#059669',
          2: '#D97706',
          3: '#0891B2',
          4: '#7C3AED',
          5: '#65A30D',
          6: '#DB2777',
          7: '#DC2626',
        },
        timeline: {
          bg:       '#080D22',
          ruler:    '#0B1230',
          playhead: '#EF4444',
          grid:     'rgba(255,255,255,0.04)',
        },
      },
      borderRadius: {
        sm:  '0.25rem',
        DEFAULT: '0.5rem',
        md:  '0.5rem',
        lg:  '0.75rem',
        xl:  '1rem',
        '2xl': '1.25rem',
        '3xl': '1.5rem',
      },
      boxShadow: {
        none: 'none',
      },
      backdropBlur: {
        xs: '2px',
        sm: '4px',
        md: '8px',
        lg: '16px',
        xl: '24px',
      },
      animation: {
        'fade-in':     'fadeIn 0.2s ease-out',
        'slide-up':    'slideUp 0.3s ease-out',
        'slide-down':  'slideDown 0.3s ease-out',
        'slide-left':  'slideLeft 0.3s ease-out',
        'slide-right': 'slideRight 0.3s ease-out',
        'scale-in':    'scaleIn 0.2s ease-out',
        'pulse-glow':  'pulseGlow 2s ease-in-out infinite',
        'shimmer':     'shimmer 1.5s infinite',
        'spin-slow':   'spin 3s linear infinite',
        'bounce-subtle': 'bounceSub 1s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%':   { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideDown: {
          '0%':   { opacity: '0', transform: 'translateY(-16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideLeft: {
          '0%':   { opacity: '0', transform: 'translateX(16px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        slideRight: {
          '0%':   { opacity: '0', transform: 'translateX(-16px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        scaleIn: {
          '0%':   { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        pulseGlow: {
          '0%, 100%': { boxShadow: '0 0 12px rgba(240,80,110,0.3)' },
          '50%':      { boxShadow: '0 0 32px rgba(240,80,110,0.7)' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        bounceSub: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%':      { transform: 'translateY(-4px)' },
        },
      },
    },
  },
  plugins: [],
}
