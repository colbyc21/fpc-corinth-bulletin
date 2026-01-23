// Dark mode toggle
const darkModeToggle = document.getElementById('darkModeToggle');
if (darkModeToggle) {
  darkModeToggle.addEventListener('click', () => {
    const isDark = document.documentElement.classList.toggle('dark');
    document.body.classList.toggle('dark');
    localStorage.setItem('darkMode', isDark);
  });
}

// Share button
const shareBtn = document.getElementById('shareBtn');
if (shareBtn) {
  shareBtn.addEventListener('click', async () => {
    const shareData = {
      title: document.title,
      text: 'Sunday Bulletin - First Presbyterian Church of Corinth',
      url: window.location.href
    };

    if (navigator.share) {
      try {
        await navigator.share(shareData);
      } catch (err) {
        // User cancelled or share failed
        console.log('Share cancelled');
      }
    } else {
      // Fallback: copy to clipboard
      try {
        await navigator.clipboard.writeText(window.location.href);
        shareBtn.textContent = 'Link Copied!';
        setTimeout(() => {
          shareBtn.textContent = 'Share Bulletin';
        }, 2000);
      } catch (err) {
        console.error('Failed to copy link');
      }
    }
  });
}

// Follow along mode toggle
const followAlongBtn = document.getElementById('followAlongBtn');
if (followAlongBtn) {
  followAlongBtn.addEventListener('click', () => {
    const isFollowAlong = document.body.classList.toggle('follow-along');
    followAlongBtn.textContent = isFollowAlong ? 'Normal Mode' : 'Follow Along Mode';
    localStorage.setItem('followAlong', isFollowAlong);
  });

  // Restore follow along state
  if (localStorage.getItem('followAlong') === 'true') {
    document.body.classList.add('follow-along');
    followAlongBtn.textContent = 'Normal Mode';
  }
}

// Service worker registration for offline support
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Service worker registration failed, offline won't work
    });
  });
}
