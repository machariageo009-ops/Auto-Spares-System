
(function () {
    var deferredPrompt = null;
    var btn = document.getElementById('installApp');
    var card = document.getElementById('installCard');

    // Hide the install button if the app is already installed
    function checkInstallation() {
        var isInstalled =
            window.matchMedia('(display-mode: standalone)').matches ||
            window.navigator.standalone === true;

        if (isInstalled && card) {
            card.style.display = 'none';
        }
    }

    checkInstallation();

    // Listen for the browser's installation prompt
    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferredPrompt = e;

        if (btn) {
            btn.style.display = 'inline-block';
        }
    });

    // Handle the install button click
    if (btn) {
        btn.addEventListener('click', async function () {
            if (deferredPrompt) {
                deferredPrompt.prompt();

                var choice = await deferredPrompt.userChoice;
                deferredPrompt = null;

                if (choice.outcome === 'accepted') {
                    btn.style.display = 'none';
                }

                return;
            }

            alert('On iPhone: open the browser Share menu and choose "Add to Home Screen". On Android Chrome, use the browser menu and choose "Install app" or "Add to Home screen".');
        });
    }

    // Hide the installation card after successful installation
    window.addEventListener('appinstalled', function () {
        if (card) {
            card.style.display = 'none';
        }
    });
})();
