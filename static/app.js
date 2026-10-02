(function(){
  var deferredPrompt=null, btn=document.getElementById('installApp'), card=document.getElementById('installCard');
  window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();deferredPrompt=e;if(btn)btn.style.display='inline-block';});
  if(btn){btn.addEventListener('click',async function(){
    if(deferredPrompt){deferredPrompt.prompt();var choice=await deferredPrompt.userChoice;deferredPrompt=null;return;}
    alert('On iPhone: open the browser Share menu and choose “Add to Home Screen”. On Android Chrome, use the browser menu and choose “Install app” or “Add to Home screen”.');
  });}
  window.addEventListener('appinstalled',function(){if(card)card.style.display='none';});
})();
