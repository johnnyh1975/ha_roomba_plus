"""iRobot's own error texts, taken from the app rather than written here.

**126 LABELS OF OUR OWN AGREED WITH THE VENDOR IN TWO CASES.** The rest
were written from observation, guesswork and older documentation, and
they were not merely differently worded:

    ours                    iRobot
    "Charging error"        "Charging Issue: unable to charge"
    "No charge current"     "Charging Issue: contacts need to be cleaned"
    "Right wheel error"     "Right wheel sensor issue"

The second column tells somebody what to do. The first tells them
something is wrong, which they already knew from the robot stopping.

SOURCE: `com.irobot.home.prime` 3.0.0 (build 3000008), the Flutter
rewrite, where the catalogue ships as plain locale JSON rather than
compiled constants. 112 codes, each with a title and an explanation, in
25 languages -- the eight this integration speaks are extracted here.

`@val` IS THE ROBOT'S NAME in iRobot's own strings, and it is left in
place: a caller that knows the name substitutes it, and one that does
not gets a sentence with a placeholder rather than a mangled one.

WHAT THIS DOES NOT REPLACE. Our own catalogue carries 75 codes the app
does not, from field reports and older firmware -- @connormxy's 236 is
not in iRobot's 112 either, so a robot can report a code its own maker
does not document. The vendor text wins where both have an entry; ours
answers where the vendor is silent.
"""

from __future__ import annotations

import re
from typing import Any, Final

#: code -> locale -> {"title", "content"}. Generated from the app's
#: locale files; not hand-edited. Regenerate rather than patch.
VENDOR_ERROR_TEXTS: Final[dict[int, dict[str, dict[str, str]]]] = {1: {'de': {'content': 'Bewegen Sie @val auf einen neuen, ebenen Untergrund. Wenn er sich bereits '
                       'auf einem ebenen Untergrund befindet, müssen Sie ihn möglicherweise neu '
                       'starten. (1)',
            'title': '@val wurde bewegt oder befindet sich auf einem unebenen Untergrund'},
     'en': {'content': 'Move\xa0@val\xa0to a new, flat surface. If it is already on a flat '
                       'surface, you may need to reboot it. (1)',
            'title': '@val\xa0moved or on an uneven surface'},
     'es': {'content': 'Mueve @val a otra superficie que sea plana. Si ya está en una superficie '
                       'plana, es posible que debas reiniciarlo. (1)',
            'title': '@val se ha movido o está en una superficie irregular'},
     'fr': {'content': 'Déplacez @val sur une nouvelle surface plane. S’il est déjà sur une '
                       'surface plane, vous devrez peut-être le redémarrer. (1)',
            'title': '@val a été déplacé ou se trouve sur une surface irrégulière'},
     'it': {'content': 'Spostare @val su una nuova superficie piana. Se è già su una superficie '
                       'piana, potrebbe essere necessario riavviarlo. (1)',
            'title': '@val è stato spostato o si trova su una superficie irregolare'},
     'nl': {'content': 'Verplaats @val naar een nieuw, vlak oppervlak. Als deze al op een vlak '
                       'oppervlak staat, moet u deze mogelijk opnieuw opstarten. (1)',
            'title': '@val is verplaatst of staat op een oneffen oppervlak'},
     'pl': {'content': 'Przenieś robota @val na nową, płaską powierzchnię. Jeśli znajduje się już '
                       'na płaskiej powierzchni, może być konieczne ponowne jego uruchomienie. (1)',
            'title': 'Robot @val został przeniesiony lub znajduje się na nierównej powierzchni'},
     'pt': {'content': 'Mova @val para uma nova superfície plana. Se já estiver numa superfície '
                       'plana, poderá ser necessário reiniciá-lo. (1)',
            'title': '@val foi movido ou está numa superfície irregular'}},
 2: {'de': {'content': 'Entfernen Sie Hindernisse oder verwickelte Fasern von der Bürste, sodass '
                       'sie sich frei drehen kann. (2)',
            'title': 'Hauptbürste klemmt'},
     'en': {'content': 'Clear obstacles or tangled fibers from the brush so it can spin freely. '
                       '(2)',
            'title': 'Main Brush stuck'},
     'es': {'content': 'Retira los obstáculos o las fibras enredadas del cepillo para que pueda '
                       'girar libremente. (2)',
            'title': 'Cepillo multisuperficie atascado'},
     'fr': {'content': 'Retirez les blocages ou les fibres emmêlées de la brosse afin qu’elle '
                       'puisse tourner librement. (2)',
            'title': 'La brosse principale est bloquée'},
     'it': {'content': 'Rimuovere gli ostacoli o le fibre aggrovigliate dalla spazzola in modo che '
                       'possa ruotare liberamente. (2)',
            'title': 'Spazzola multiuso bloccata'},
     'nl': {'content': 'Verwijder obstakels of verwarde vezels uit de borstel, zodat deze vrij kan '
                       'draaien. (2)',
            'title': 'Hoofdborstel zit vast'},
     'pl': {'content': 'Usuń przeszkody lub splątane włókna ze szczotki, aby mogła swobodnie się '
                       'obracać. (2)',
            'title': 'Szczotka główna jest zablokowana'},
     'pt': {'content': 'Remova obstáculos ou fibras emaranhadas da escova para que possa rodar '
                       'livremente. (2)',
            'title': 'Escova principal bloqueada'}},
 4: {'de': {'content': 'Drücken Sie das Rad einige Male nach oben und unten und drehen Sie es '
                       'dann, um eingeklemmten Schmutz zu lösen. Es sollte sich frei drehen '
                       'lassen. (4)',
            'title': 'Linkes Rad klemmt'},
     'en': {'content': 'Push Left/Right Wheel up and down a few times, then spin it to loosen '
                       'trapped debris. It should spin freely. (4)',
            'title': 'Left Wheel stuck'},
     'es': {'content': 'Empuja la rueda hacia arriba y hacia abajo unas cuantas veces, y luego '
                       'gírala para soltar los residuos atrapados. Debería girar libremente. (4)',
            'title': 'Rueda izquierda atascada'},
     'fr': {'content': 'Actionnez la roue de haut en bas à plusieurs reprises, puis faites-la '
                       'tourner pour déloger les débris coincés. Elle doit tourner librement. (4)',
            'title': 'La roue gauche est bloquée'},
     'it': {'content': 'Spingere la ruota su e giù un paio di volte, quindi farla girare per '
                       'estrarre i detriti incastrati. Dovrebbe girare liberamente. (4)',
            'title': 'Ruota sinistra bloccata'},
     'nl': {'content': 'Duw het wiel een paar keer op en neer en draai het vervolgens rond om '
                       'vastzittend vuil los te maken. Het moet vrij kunnen draaien. (4)',
            'title': 'Linkerwiel zit vast'},
     'pl': {'content': 'Popchnij kółko w górę i w dół kilka razy, a następnie obróć nim, aby '
                       'poluzować uwięzione zanieczyszczenia. Powinno swobodnie się obracać. (4)',
            'title': 'Lewe kółko jest zablokowane'},
     'pt': {'content': 'Empurre a roda para cima e para baixo algumas vezes e depois rode-a para '
                       'soltar os resíduos presos. Deve rodar livremente. (4)',
            'title': 'Roda esquerda bloqueada'}},
 5: {'de': {'content': 'Drücken Sie das Rad einige Male nach oben und unten und drehen Sie es '
                       'dann, um eingeklemmten Schmutz zu lösen. Es sollte sich frei drehen '
                       'lassen. (5)',
            'title': 'Rechtes Rad klemmt'},
     'en': {'content': 'Push Left/Right Wheel up and down a few times, then spin it to loosen '
                       'trapped debris. It should spin freely. (5)',
            'title': 'Right Wheel stuck'},
     'es': {'content': 'Empuja la rueda hacia arriba y hacia abajo unas cuantas veces, y luego '
                       'gírala para soltar los residuos atrapados. Debería girar libremente. (5)',
            'title': 'Rueda derecha atascada'},
     'fr': {'content': 'Actionnez la roue de haut en bas à plusieurs reprises, puis faites-la '
                       'tourner pour déloger les débris coincés. Elle doit tourner librement. (5)',
            'title': 'La roue droite est bloquée'},
     'it': {'content': 'Spingere la ruota su e giù un paio di volte, quindi farla girare per '
                       'estrarre i detriti incastrati. Dovrebbe girare liberamente. (5)',
            'title': 'La ruota destra è bloccata'},
     'nl': {'content': 'Duw het wiel een paar keer op en neer en draai het vervolgens rond om '
                       'vastzittend vuil los te maken. Het moet vrij kunnen draaien. (5)',
            'title': 'Rechterwiel zit vast'},
     'pl': {'content': 'Popchnij kółko w górę i w dół kilka razy, a następnie obróć nim, aby '
                       'poluzować uwięzione zanieczyszczenia. Powinno swobodnie się obracać. (5)',
            'title': 'Prawe koło jest zablokowane'},
     'pt': {'content': 'Empurre a roda para cima e para baixo algumas vezes e depois rode-a para '
                       'soltar os resíduos presos. Deve rodar livremente. Deve rodar livremente. '
                       '(5)',
            'title': 'Roda direita bloqueada'}},
 6: {'de': {'content': 'Reinigen Sie die unteren Absturzsensoren mit einem weichen, trockenen '
                       'Tuch, damit Treppen korrekt erkannt werden können. Stellen Sie @val auf '
                       'den Boden und drücken Sie die Starttaste, um die Reinigung fortzusetzen. '
                       '(6)',
            'title': 'Abgrundsensoren müssen gereinigt werden'},
     'en': {'content': 'Clean the bottom Cliff Sensors with a soft, dry cloth so stairs can be '
                       'accurately detected. Place\xa0@val\xa0on the floor and press the start '
                       'button to resume cleaning. (6)',
            'title': 'Clean Cliff Sensors'},
     'es': {'content': 'Limpia los sensores de desnivel inferiores con un paño suave y seco para '
                       'que las escaleras se detecten con precisión. Coloca @val en el suelo y '
                       'pulsa el botón de inicio para reanudar la limpieza. (6)',
            'title': 'Es necesario limpiar los sensores anticaída'},
     'fr': {'content': 'Nettoyez les capteurs de vide situés sous le robot avec un chiffon doux et '
                       'sec afin que les escaliers puissent être détectés avec précision. Placez '
                       '@val sur le sol et appuyez sur le bouton Démarrer pour reprendre le '
                       'nettoyage. (6)',
            'title': 'Les capteurs de vide ont besoin d’être nettoyés'},
     'it': {'content': 'Pulisci i sensori di dislivello inferiori con un panno morbido e asciutto '
                       'affinché le scale vengano rilevate con precisione. Posiziona @val sul '
                       'pavimento e premi il pulsante di avvio per riprendere la pulizia. (6)',
            'title': 'I sensori di caduta sono sporchi'},
     'nl': {'content': 'Maak de onderste afgrondsensoren schoon met een zachte, droge doek zodat '
                       'trappen nauwkeurig kunnen worden gedetecteerd. Plaats @val op de vloer en '
                       'druk op de startknop om het schoonmaken te hervatten. (6)',
            'title': 'Afgrondsensoren moeten worden schoongemaakt'},
     'pl': {'content': 'Wyczyść dolne czujniki uskoku miękką, suchą ściereczką, aby schody były '
                       'dokładnie wykrywane. Umieść robota @val na podłodze i naciśnij przycisk '
                       'start, aby wznowić sprzątanie. (6)',
            'title': 'Czujniki spadku wymagają wyczyszczenia'},
     'pt': {'content': 'Limpe os sensores de desnível inferiores com um pano macio e seco para que '
                       'as escadas possam ser detetadas com precisão. Coloque @val no chão e prima '
                       'o botão Iniciar para retomar a limpeza. (6)',
            'title': 'Sensores de desnível precisam de limpeza'}},
 7: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Nehmen Sie ihn von der '
                       'Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden lang '
                       'gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (7)',
            'title': 'Problem mit linkem Radsensor'},
     'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, hold '
                       'the Power button for 10s then 3s. (7)',
            'title': 'Left wheel sensor issue'},
     'es': {'content': 'Reinicia @val para solucionarlo. Retíralo de la base y, a continuación, '
                       'mantén pulsado el botón de encendido durante 10\xa0segundos. Luego '
                       'mantenlo presionado 3s. (7)',
            'title': 'Problema con el sensor de la rueda izquierda'},
     'fr': {'content': 'Redémarrez @val pour effacer. Retirez-le de la station d’accueil, puis '
                       'maintenez le bouton d’alimentation enfoncé pendant 10 secondes. Puis '
                       'maintenez-le enfoncé pendant 3s. (7)',
            'title': 'Problème de capteur de la roue gauche'},
     'it': {'content': 'Riavviare @val per risolverlo. Rimuoverlo dalla base, quindi tenere '
                       'premuto il pulsante di accensione per 10 secondi. Quindi tienilo premuto '
                       'per 3s. (7)',
            'title': 'Problema con il sensore della ruota sinistra'},
     'nl': {'content': 'Start @val opnieuw op om te wissen. Haal het apparaat van het basisstation '
                       'en houd de aan/uit-knop 10 seconden ingedrukt. Houd deze daarna 3s '
                       'ingedrukt. (7)',
            'title': 'Probleem met linker wielsensor'},
     'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia problemu. Wyjmij go ze '
                       'stacji dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania '
                       'przez 10\xa0sekund. Następnie przytrzymaj przez 3s. (7)',
            'title': 'Wystąpił problem z czujnikiem lewego kółka'},
     'pt': {'content': 'Reinicie @val para corrigir. Retire-o da base e depois prima sem soltar o '
                       'botão de alimentação durante 10 segundos. Em seguida, mantenha premido por '
                       '3s. (7)',
            'title': 'Problema no sensor da roda esquerda'}},
 9: {'de': {'content': 'Entfernen Sie alle Objekte, die möglicherweise hinter dem vorderen '
                       'Stoßfänger von @val verkantet sind. (9)',
            'title': 'Stoßfänger steckt fest'},
     'en': {'content': 'Clear any objects that may be wedged behind\xa0@val’s front bumper. (9)',
            'title': 'Bumper is stuck'},
     'es': {'content': 'Retira cualquier objeto que pueda estar encajado detrás del parachoques '
                       'frontal de @val. (9)',
            'title': 'El parachoques está atascado'},
     'fr': {'content': 'Retirez tout objet qui pourrait être coincé derrière le pare-chocs avant '
                       'de @val. (9)',
            'title': 'Le pare-chocs est bloqué'},
     'it': {'content': 'Rimuovere eventuali oggetti incastrati dietro il paraurti anteriore di '
                       '@val. (9)',
            'title': 'Paraurti incastrato'},
     'nl': {'content': 'Verwijder alle voorwerpen die mogelijk achter de voorbumper van @val '
                       'vastzitten. (9)',
            'title': 'Bumper zit vast'},
     'pl': {'content': 'Usuń wszelkie przedmioty, które mogły utknąć za przednim zderzakiem robota '
                       '@val. (9)',
            'title': 'Zderzak jest zablokowany'},
     'pt': {'content': 'Remova quaisquer objetos que possam estar presos atrás do para-choques '
                       'frontal de @val. (9)',
            'title': 'Para-choques bloqueado'}},
 10: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Nehmen Sie ihn von der '
                        'Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden lang '
                        'gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (10)',
             'title': 'Problem mit rechtem Radsensor'},
      'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, hold '
                        'the Power button for 10s then 3s. (10)',
             'title': 'Right wheel sensor issue'},
      'es': {'content': 'Reinicia @val para solucionarlo. Retíralo de la base y, a continuación, '
                        'mantén pulsado el botón de encendido durante 10\xa0segundos. Luego '
                        'mantenlo presionado 3s. (10)',
             'title': 'Problema con el sensor de la rueda derecha'},
      'fr': {'content': 'Redémarrez @val pour effacer. Retirez-le de la station d’accueil, puis '
                        'maintenez le bouton d’alimentation enfoncé pendant 10 secondes. Puis '
                        'maintenez-le enfoncé pendant 3s. (10)',
             'title': 'Problème de capteur de la roue droite'},
      'it': {'content': 'Riavviare @val per risolverlo. Rimuoverlo dalla base, quindi tenere '
                        'premuto il pulsante di accensione per 10 secondi. Quindi tienilo premuto '
                        'per 3s. (10)',
             'title': 'Problema con il sensore della ruota destra'},
      'nl': {'content': 'Start @val opnieuw op om te wissen. Haal het apparaat van het '
                        'basisstation en houd de aan/uit-knop 10 seconden ingedrukt. Houd deze '
                        'daarna 3s ingedrukt. (10)',
             'title': 'Probleem met rechterwielsensor'},
      'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia problemu. Wyjmij go ze '
                        'stacji dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania '
                        'przez 10\xa0sekund. Następnie przytrzymaj przez 3s. (10)',
             'title': 'Problem z czujnikiem prawego kółka'},
      'pt': {'content': 'Reinicie @val para corrigir. Retire-o da base e depois prima sem soltar o '
                        'botão de alimentação durante 10 segundos. Em seguida, mantenha premido '
                        'por 3s. (10)',
             'title': 'Problema no sensor da roda direita'}},
 12: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Entfernen Sie ihn von der '
                        'Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden lang '
                        'gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (12)',
             'title': 'Abgrundsensor blockiert'},
      'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, hold '
                        'the Power button for 10s then 3s. (12)',
             'title': 'Cliff sensor stall'},
      'es': {'content': 'Reinicia @val para solucionarlo. Retíralo de la base y mantén pulsado el '
                        'botón de encendido durante 10\xa0segundos. Luego mantenlo presionado 3s. '
                        '(12)',
             'title': 'Bloqueo del sensor anticaída'},
      'fr': {'content': 'Redémarrez @val pour effacer. Retirez-le de la station d’accueil, puis '
                        'maintenez le bouton d’alimentation enfoncé pendant 10 secondes. Puis '
                        'maintenez-le enfoncé pendant 3s. (12)',
             'title': 'Capteur de vide bloqué'},
      'it': {'content': 'Riavviare @val per risolverlo. Rimuovere dalla base, quindi tenere '
                        'premuto il pulsante di accensione per 10 secondi. Quindi tienilo premuto '
                        'per 3s. (12)',
             'title': 'Sensore di caduta bloccato'},
      'nl': {'content': 'Start @val opnieuw op om te wissen. Haal het van het basisstation en houd '
                        'de aan/uit-knop 10 seconden ingedrukt. Houd deze daarna 3s ingedrukt. '
                        '(12)',
             'title': 'Storing afgrondsensor'},
      'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia problemu. Wyjmij ze stacji '
                        'dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania przez '
                        '10\xa0sekund. Następnie przytrzymaj przez 3s. (12)',
             'title': 'Zatrzymanie spowodowane zadziałaniem czujnika spadku'},
      'pt': {'content': 'Reinicie @val para corrigir. Retire da base e depois prima sem soltar o '
                        'botão de alimentação durante 10 segundos. Em seguida, mantenha premido '
                        'por 3s. (12)',
             'title': 'Bloqueio dos sensores de precipício'}},
 14: {'de': {'content': 'Bitte stellen Sie sicher, dass der Behälter von @val eingesetzt ist und '
                        'die Sensoren sauber sind. Verwenden Sie zur Reinigung ein weiches, '
                        'trockenes Tuch.(14)',
             'title': 'Der Behälter von @val fehlt'},
      'en': {'content': 'Please make sure\xa0@val’s bin is installed and the sensors are clean. '
                        'Use a soft, dry cloth to clean.(14)',
             'title': '@val’s bin is missing'},
      'es': {'content': 'Asegúrate de que el depósito de @val esté instalado y de que los sensores '
                        'estén limpios. Usa un paño suave y seco para limpiarlos.(14)',
             'title': 'Falta el depósito de @val'},
      'fr': {'content': 'Veuillez vous assurer que le bac de @val est installé et que les capteurs '
                        'sont propres. Utilisez un chiffon doux et sec pour nettoyer.(14)',
             'title': 'Le bac de @val est manquant'},
      'it': {'content': 'Assicurati che il cestino di @val sia installato e che i sensori siano '
                        'puliti. Utilizzare un panno morbido e asciutto per pulire.(14)',
             'title': 'Il cestino di @val è mancante'},
      'nl': {'content': 'Zorg ervoor dat de opvangbak van @val is geïnstalleerd en dat de sensoren '
                        'schoon zijn. Gebruik een zachte, droge doek om schoon te maken.(14)',
             'title': 'De opvangbak van @val ontbreekt'},
      'pl': {'content': 'Upewnij się, że pojemnik robota @val jest zamontowany, a czujniki czyste. '
                        'Do czyszczenia użyj miękkiej, suchej ściereczki.(14)',
             'title': 'Brak pojemnika robota @val'},
      'pt': {'content': 'Certifique-se de que o depósito de @val está instalado e que os sensores '
                        'estão limpos. Utilize um pano macio e seco para limpar. (14)',
             'title': 'O depósito de @val está em falta'}},
 16: {'de': {'content': 'Bewegen Sie @val auf einen neuen, ebenen Untergrund. Wenn er sich bereits '
                        'auf einem ebenen Untergrund befindet, müssen Sie ihn möglicherweise neu '
                        'starten. (16)',
             'title': '@val wurde bewegt oder befindet sich auf einem unebenen Untergrund'},
      'en': {'content': 'Move\xa0@val\xa0to a new, flat surface. If it is already on a flat '
                        'surface, you may need to reboot it. (16)',
             'title': '@val\xa0was moved or is on an uneven surface'},
      'es': {'content': 'Mueve @val a otra superficie que sea plana. Si ya está en una superficie '
                        'plana, es posible que debas reiniciarlo. (16)',
             'title': '@val se ha movido o está en una superficie irregular'},
      'fr': {'content': 'Déplacez @val sur une nouvelle surface plane. S’il est déjà sur une '
                        'surface plane, vous devrez peut-être le redémarrer. (16)',
             'title': '@val a été déplacé ou se trouve sur une surface irrégulière'},
      'it': {'content': 'Spostare @val su una nuova superficie piana. Se è già su una superficie '
                        'piana, potrebbe essere necessario riavviarlo. (16)',
             'title': '@val è stato spostato o si trova su una superficie irregolare'},
      'nl': {'content': 'Verplaats @val naar een nieuw, vlak oppervlak. Als deze al op een vlak '
                        'oppervlak staat, moet u het mogelijk opnieuw opstarten. (16)',
             'title': '@val is verplaatst of staat op een oneffen oppervlak'},
      'pl': {'content': 'Przenieś robota @val na nową, płaską powierzchnię. Jeśli znajduje się już '
                        'na płaskiej powierzchni, może być konieczne ponowne jego uruchomienie. '
                        '(16)',
             'title': 'Robot @val został przeniesiony lub znajduje się na nierównej powierzchni'},
      'pt': {'content': 'Mova @val para uma nova superfície plana. Se já estiver numa superfície '
                        'plana, poderá ser necessário reiniciá-lo. (16)',
             'title': '@val foi movido ou está numa superfície irregular'}},
 18: {'de': {'content': 'Stellen Sie sicher, dass der Pfad frei ist, damit @val zu seiner '
                        'Dockingstation zurückkehren kann. Überprüfen Sie, ob die Dockingstation '
                        'eingesteckt ist und sich an ihrem ursprünglichen Standort befindet. (18)',
             'title': '@val konnte nicht zur Dockingstation zurückkehren. Bewegen Sie ihn und '
                      'stellen Sie ihn zum Laden auf die Dockingstation.'},
      'en': {'content': 'Make sure the path is clear for\xa0@val\xa0to return to its dock. Check '
                        'that the dock is plugged in and in its original location. (18)',
             'title': "@val\xa0couldn't return to Dock. Move and place it on the Dock for "
                      'charging.'},
      'es': {'content': 'Asegúrate de que no haya obstáculos en el camino de vuelta a la base de '
                        '@val. Comprueba que la base esté enchufada y en su ubicación original. '
                        '(18)',
             'title': '@val no ha podido volver a la base. Muévelo y colócalo en la base para '
                      'cargarlo.'},
      'fr': {'content': 'Assurez-vous que le chemin est dégagé pour que @val puisse retourner à sa '
                        'station d’accueil. Vérifiez que la station d’accueil est branchée et '
                        'qu’elle se trouve à son emplacement d’origine. (18)',
             'title': '@val n’a pas pu retourner à la station d’accueil. Déplacez-le et placez-le '
                      'sur la station d’accueil pour le charger.'},
      'it': {'content': 'Assicurarsi che il percorso sia libero affinché @val possa tornare alla '
                        'sua base. Controllare che la base sia collegata e si trovi nella '
                        'posizione originale. (18)',
             'title': '@val non è riuscito a tornare alla base. Spostalo e posizionalo sulla base '
                      'per la ricarica.'},
      'nl': {'content': 'Zorg ervoor dat het pad vrij is zodat @val kan terugkeren naar zijn dock. '
                        'Controleer of het dock is aangesloten en op de oorspronkelijke locatie '
                        'staat. (18)',
             'title': '@val kon niet terugkeren naar het basisstation. Verplaats hem en plaats hem '
                      'op het basisstation om op te laden.'},
      'pl': {'content': 'Upewnij się, że droga jest wolna, aby robot @val mógł wrócić do stacji '
                        'dokującej. Sprawdź, czy stacja dokująca jest podłączona do zasilania i '
                        'znajduje się w swoim pierwotnym miejscu. (18)',
             'title': 'Robot @val nie mógł wrócić do stacji dokującej. Przesuń go i umieść na '
                      'stacji dokującej w celu ładowania.'},
      'pt': {'content': 'Certifique-se de que o caminho está livre para @val regressar à base. '
                        'Verifique se a base está ligada e na sua localização original. (18)',
             'title': '@val não conseguiu regressar à base. Mova-o e coloque-o na base para '
                      'carregar.'}},
 19: {'de': {'content': '@val konnte seine Dockingstation nicht verlassen. Räumen Sie Hindernisse '
                        'um die Dockingstation herum aus dem Weg, damit der Roboter genug Platz '
                        'zum An- und Abdocken hat. (19)',
             'title': 'Verlassen der Dockingstation nicht möglich: Hindernis im Weg'},
      'en': {'content': '@val\xa0was unable to leave its dock. Clear obstacles around the dock so '
                        'it has enough room to come and go. (19)',
             'title': 'Unable to leave dock: obstacle in the way'},
      'es': {'content': '@val no ha podido salir de su base. Despeja los obstáculos en torno a la '
                        'base dejando espacio suficiente para entrar y salir. (19)',
             'title': 'No se puede salir de la base: hay un obstáculo en el camino'},
      'fr': {'content': '@val n’a pas pu quitter sa station d’accueil. Dégagez les obstacles '
                        'autour de la station d’accueil pour qu’il ait suffisamment d’espace pour '
                        'circuler. (19)',
             'title': 'Impossible de quitter la station d’accueil : obstacle sur le chemin'},
      'it': {'content': '@val non è riuscito a lasciare la base. Rimuovere gli ostacoli intorno '
                        'alla base in modo che il robot abbia spazio a sufficienza per eseguire le '
                        'manovre di ingresso/uscita. (19)',
             'title': 'Impossibile lasciare la base: un ostacolo blocca il passaggio'},
      'nl': {'content': '@val kon zijn dock niet verlaten. Verwijder obstakels rondom het '
                        'basisstation zodat het voldoende ruimte heeft om in en uit te rijden. '
                        '(19)',
             'title': 'Kan het dock niet verlaten: er bevindt zich een obstakel in de weg'},
      'pl': {'content': 'Robot @val nie mógł opuścić stacji dokującej. Usuń przeszkody wokół '
                        'stacji dokującej, aby zapewnić robotowi wystarczającą ilość miejsca do '
                        'wyjazdu i powrotu. (19)',
             'title': 'Nie może opuścić stacji dokującej: przeszkoda na drodze'},
      'pt': {'content': '@val não conseguiu sair da base. Remova obstáculos à volta da base para '
                        'que tenha espaço suficiente para entrar e sair. (19)',
             'title': 'Não é possível sair da base: obstáculo no caminho'}},
 22: {'de': {'content': 'Bewegen Sie ihn in einen freien Bereich und drücken Sie die '
                        'Ein-/Aus-Taste, um fortzufahren. Entfernen Sie Hindernisse und öffnen Sie '
                        'Türen. (22)',
             'title': '@val steckt fest'},
      'en': {'content': 'Move it to an open area and press the Power button to resume. Clear '
                        'obstacles and open doors. (22)',
             'title': '@val\xa0is stuck'},
      'es': {'content': 'Muévelo a un área despejada y pulsa el botón de encendido para reanudar '
                        'su actividad. Retira los obstáculos y abre las puertas. (22)',
             'title': '@val está atascado'},
      'fr': {'content': 'Déplacez-le vers une zone dégagée et appuyez sur le bouton d’alimentation '
                        'pour reprendre. Dégagez les obstacles et ouvrez les portes. (22)',
             'title': '@val est bloqué'},
      'it': {'content': "Spostarlo in un'area aperta e premere il pulsante di accensione per "
                        'riprendere il funzionamento. Rimuovere gli ostacoli e aprire le porte. '
                        '(22)',
             'title': '@val è bloccato'},
      'nl': {'content': 'Verplaats het naar een open ruimte en druk op de aan-uitknop om door te '
                        'gaan. Verwijder obstakels en open deuren. (22)',
             'title': '@val zit vast'},
      'pl': {'content': 'Przenieś na otwartą przestrzeń i naciśnij przycisk zasilania, aby '
                        'wznowić. Usuń przeszkody i otwórz drzwi. (22)',
             'title': 'Robot @val jest zablokowany'},
      'pt': {'content': 'Mova-o para uma área aberta e prima o botão de alimentação para retomar. '
                        'Remova os obstáculos e abra as portas. (22)',
             'title': '@val está preso'}},
 24: {'de': {'content': 'Stellen Sie @val auf einen ebenen Untergrund und drücken Sie die '
                        'Ein-/Aus-Taste, um fortzufahren. (24)',
             'title': 'Navigationsproblem'},
      'en': {'content': 'Move\xa0@val\xa0to a flat surface and press the Power button to resume. '
                        '(24)',
             'title': 'Navigation Issue'},
      'es': {'content': 'Mueve @val a una superficie plana y pulsa el botón de encendido para '
                        'reanudar su actividad. (24)',
             'title': 'Problema de navegación'},
      'fr': {'content': 'Déplacez @val sur une surface plane et appuyez sur le bouton '
                        'd’alimentation pour reprendre. (24)',
             'title': 'Problème de navigation'},
      'it': {'content': 'Spostare @val su una superficie piana e premere il pulsante di accensione '
                        'per riprendere il funzionamento. (24)',
             'title': 'Problema di navigazione'},
      'nl': {'content': 'Plaats @val op een vlakke ondergrond en druk op de aan/uit-knop om door '
                        'te gaan. (24)',
             'title': 'Navigatieprobleem'},
      'pl': {'content': 'Przenieś robota @val na płaską powierzchnię i naciśnij przycisk '
                        'zasilania, aby wznowić. (24)',
             'title': 'Problem z nawigacją'},
      'pt': {'content': 'Mova @val para uma superfície plana e prima o botão de alimentação para '
                        'retomar. (24)',
             'title': 'Problema de navegação'}},
 26: {'de': {'content': 'Der Filter von @val ist möglicherweise verstopft. Entfernen Sie den '
                        'Filter aus dem Staubbehälter und klopfen Sie ihn über einem Mülleimer '
                        'aus, um angesammelten Schmutz zu entfernen. (26)',
             'title': 'Saugmotor blockiert'},
      'en': {'content': '@val’s filter may be clogged. Remove filter from dust bin and tap it out '
                        'over a trash bin to clear built-up debris. (26)',
             'title': 'Vacuum motor is stalled'},
      'es': {'content': 'Es posible que el filtro de @val esté obstruido. Retira el filtro del '
                        'depósito de polvo y golpéalo suavemente sobre un cubo de basura para '
                        'eliminar la suciedad acumulada. (26)',
             'title': 'El motor de aspiración está atascado'},
      'fr': {'content': 'Le filtre de @val est peut-être obstrué. Retirez le filtre du bac à '
                        'poussière et tapotez-le au-dessus d’une poubelle pour éliminer les débris '
                        'accumulés. (26)',
             'title': 'Le moteur d’aspiration est bloqué'},
      'it': {'content': 'Il filtro di @val potrebbe essere ostruito. Rimuovere il filtro dal '
                        'contenitore della polvere e sbatterlo su un cestino dei rifiuti per '
                        'rimuovere i detriti accumulati. (26)',
             'title': 'Il motore di aspirazione è bloccato'},
      'nl': {'content': 'Het filter van @val is mogelijk verstopt. Verwijder het filter uit de '
                        'stofbak en klop het uit boven een vuilnisbak om opgehoopt vuil te '
                        'verwijderen. (26)',
             'title': 'Zuigmotor is vastgelopen'},
      'pl': {'content': 'Filtr robota @val może być zatkany. Wyjmij filtr z pojemnika na kurz i '
                        'wytrzep go nad koszem na śmieci, aby usunąć nagromadzony brud. (26)',
             'title': 'Silnik odkurzacza jest zablokowany'},
      'pt': {'content': 'O filtro de @val pode estar obstruído. Retire o filtro do depósito de pó '
                        'e bata-o ligeiramente sobre o caixote do lixo para remover os resíduos '
                        'acumulados. (26)',
             'title': 'Motor de aspiração bloqueado'}},
 29: {'de': {'content': 'Dies kann bis zu 20 Minuten dauern. Wenn es länger als 20 Minuten dauert, '
                        'starten Sie @val neu. (29)',
             'title': 'Roboter-Software wird aktualisiert'},
      'en': {'content': 'This can take up to 20 minutes. If it takes longer than 20 minutes, '
                        'reboot\xa0@val. (29)',
             'title': 'Robot software is updating'},
      'es': {'content': 'Este proceso puede tardar hasta 20\xa0minutos. Si tarda más de 20\xa0'
                        'minutos, reinicia @val. (29)',
             'title': 'El software del robot se está actualizando'},
      'fr': {'content': 'Cela peut prendre jusqu’à 20 minutes. Si cela prend plus de 20 minutes, '
                        'redémarrez @val. (29)',
             'title': 'Mise à jour du logiciel du robot en cours'},
      'it': {'content': 'Potrebbe richiedere fino a 20 minuti. Se impiega più di 20 minuti, '
                        'riavviare @val. (29)',
             'title': 'Aggiornamento del software del robot in corso'},
      'nl': {'content': 'Dit kan tot 20 minuten duren. Als het langer dan 20 minuten duurt, start '
                        'je @val opnieuw op. (29)',
             'title': 'Robotsoftware wordt bijgewerkt'},
      'pl': {'content': 'Może to potrwać maksymalnie 20\xa0minut. Jeśli potrwa to dłużej niż 20\xa0'
                        'minut, uruchom ponownie @val. (29)',
             'title': 'Trwa aktualizacja oprogramowania robota'},
      'pt': {'content': 'Isto pode demorar até 20 minutos. Se demorar mais de 20 minutos, reinicie '
                        '@val. (29)',
             'title': 'O software do robô está a ser atualizado'}},
 30: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Nehmen Sie ihn von der '
                        'Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden lang '
                        'gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (30)',
             'title': 'Saugmotor-Problem'},
      'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, hold '
                        'the Power button for 10s then 3s. (30)',
             'title': 'Vacuum motor issue'},
      'es': {'content': 'Reinicia @val para solucionarlo. Retíralo de la base y, a continuación, '
                        'mantén pulsado el botón de encendido durante 10\xa0segundos. Luego '
                        'mantenlo presionado 3s. (30)',
             'title': 'Problema del motor de aspiración'},
      'fr': {'content': 'Redémarrez @val pour effacer. Retirez-le de la station d’accueil, puis '
                        'maintenez le bouton d’alimentation enfoncé pendant 10 secondes. Puis '
                        'maintenez-le enfoncé pendant 3s. (30)',
             'title': 'Problème du moteur d’aspiration'},
      'it': {'content': 'Riavviare @val per risolverlo. Rimuoverlo dalla base, quindi tenere '
                        'premuto il pulsante di accensione per 10 secondi. Quindi tienilo premuto '
                        'per 3s. (30)',
             'title': 'Problema al motore di aspirazione'},
      'nl': {'content': 'Start @val opnieuw op om te wissen. Haal het apparaat van het '
                        'basisstation en houd de aan/uit-knop 10 seconden ingedrukt. Houd deze '
                        'daarna 3s ingedrukt. (30)',
             'title': 'Probleem met vacuümmotor'},
      'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia problemu. Wyjmij go ze '
                        'stacji dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania '
                        'przez 10\xa0sekund. Następnie przytrzymaj przez 3s. (30)',
             'title': 'Problem z silnikiem odkurzacza'},
      'pt': {'content': 'Reinicie @val para corrigir. Retire-o da base e depois prima sem soltar o '
                        'botão de alimentação durante 10 segundos. Em seguida, mantenha premido '
                        'por 3s. (30)',
             'title': 'Problema no motor de aspiração'}},
 32: {'de': {'content': 'Stellen Sie sicher, dass @val die für diese Routine verwendeten Bereiche '
                        'auf der Karte erreichen kann. Dieses Problem kann bei mehreren Karten '
                        'auftreten, die nicht miteinander verbunden sind. (32)',
             'title': '@val konnte die angeforderten Kartenbereiche nicht erreichen'},
      'en': {'content': 'Make sure\xa0@val\xa0can reach the areas on the map used for this '
                        'routine. This issue can happen with multiple maps that don’t connect. '
                        '(32)',
             'title': '@val\xa0couldn’t get to the requested map areas'},
      'es': {'content': 'Asegúrate de que @val pueda llegar a las áreas del mapa utilizadas para '
                        'esta rutina. Este problema puede producirse cuando hay varios mapas que '
                        'no se conectan. (32)',
             'title': '@val no ha podido llegar a las áreas del mapa solicitadas'},
      'fr': {'content': 'Vérifiez que @val peut atteindre les zones de la carte utilisée pour '
                        'cette routine. Ce problème peut se produire si plusieurs cartes ne sont '
                        'pas connectées. (32)',
             'title': '@val n’a pas pu accéder aux zones de la carte demandées'},
      'it': {'content': 'Assicurarsi che @val possa raggiungere le aree sulla mappa utilizzate per '
                        'questa routine. Questo problema può verificarsi con più mappe che non si '
                        'connettono. (32)',
             'title': '@val non ha potuto raggiungere le aree della mappa richieste'},
      'nl': {'content': 'Zorg ervoor dat @val de gebieden op de kaart die voor deze routine worden '
                        'gebruikt, kan bereiken. Dit probleem kan optreden bij meerdere kaarten '
                        'die niet met elkaar verbonden zijn. (32)',
             'title': '@val kon de gevraagde kaartgebieden niet bereiken'},
      'pl': {'content': 'Upewnij się, że robot @val może dotrzeć do obszarów na mapie używanych w '
                        'tej rutynie. Ten problem może wystąpić w przypadku wielu map, które się '
                        'nie łączą. (32)',
             'title': 'Robot @val nie mógł dotrzeć do żądanych obszarów na mapie'},
      'pt': {'content': 'Certifique-se de que o @val consegue chegar às áreas do mapa utilizadas '
                        'para esta rotina. Este problema pode ocorrer com vários mapas que não '
                        'estão ligados. (32)',
             'title': '@val não conseguiu aceder às áreas do mapa solicitadas'}},
 33: {'de': {'content': 'Stellen Sie sicher, dass die Türen vollständig geöffnet sind und um Möbel '
                        'herum genug Platz für @val vorhanden ist, um Fahrmanöver auszuführen. '
                        '(33)',
             'title': '@val wurde an Möbeln oder einer Tür eingeklemmt'},
      'en': {'content': 'Make sure doors are open all the way and there is enough space around '
                        'furniture for\xa0@val\xa0to maneuver around. (33)',
             'title': '@val\xa0got trapped by furniture or a door'},
      'es': {'content': 'Asegúrate de que las puertas estén completamente abiertas y de que haya '
                        'suficiente espacio alrededor de los muebles para que @val pueda '
                        'maniobrar. (33)',
             'title': '@val se ha quedado atrapado con un mueble o una puerta'},
      'fr': {'content': 'Assurez-vous que les portes sont complètement ouvertes et qu’il y a '
                        'suffisamment d’espace autour des meubles pour que @val puisse se '
                        'déplacer. (33)',
             'title': '@val est resté coincé par un meuble ou une porte'},
      'it': {'content': 'Assicurarsi che le porte siano completamente aperte e che ci sia spazio '
                        'sufficiente intorno ai mobili per consentire a @val di muoversi. (33)',
             'title': '@val si è incastrato in un mobile o una porta'},
      'nl': {'content': 'Zorg ervoor dat deuren helemaal openstaan en dat er genoeg ruimte rond de '
                        'meubels is voor @val om te manoeuvreren. (33)',
             'title': '@val zat klem door meubels of een deur'},
      'pl': {'content': 'Sprawdź, czy drzwi są całkowicie otwarte, a wokół mebli jest '
                        'wystarczająco dużo miejsca, aby robot @val mógł się przemieszczać. (33)',
             'title': 'Robot @val utknął pod meblem lub drzwiami'},
      'pt': {'content': 'Certifique-se de que as portas estão totalmente abertas e que existe '
                        'espaço suficiente à volta dos móveis para @val se movimentar. (33)',
             'title': '@val ficou preso em mobiliário ou numa porta'}},
 35: {'de': {'content': 'Bringen Sie den Mopp von @val an, um das Wischen zu aktivieren. (35)',
             'title': 'Kein Mopp angebracht'},
      'en': {'content': "Attach\xa0@val's mop to enable mopping. (35)", 'title': 'No mop attached'},
      'es': {'content': 'Coloca la mopa de @val para poder fregar. (35)',
             'title': 'Mopa no instalada'},
      'fr': {'content': 'Fixez la serpillière de @val pour activer le nettoyage à la serpillière. '
                        '(35)',
             'title': 'Aucune serpillière fixée'},
      'it': {'content': 'Inserire il panno di lavaggio di @val per abilitare il lavaggio. (35)',
             'title': 'Nessun panno di lavaggio inserito'},
      'nl': {'content': 'Bevestig de dweil van @val om te dweilen. (35)',
             'title': 'Geen dweil bevestigd'},
      'pl': {'content': 'Podłącz mopa do robota @val, aby umożliwić mycie mopem. (35)',
             'title': 'Nie podłączono mopa'},
      'pt': {'content': 'Instale a mopa de @val para ativar a lavagem. (35)',
             'title': 'Sem mopa instalada'}},
 36: {'de': {'content': 'Entleeren Sie den Behälter von @val und entfernen Sie mögliche Blockaden '
                        'am Staubverdichter und Kanal. (36)',
             'title': 'Behälter möglicherweise voll oder Schmutz blockiert den Kanal'},
      'en': {'content': 'Empty\xa0@val’s Dustbin and clear any possible obstructions to the Dust '
                        'Compactor and Air Duct. (36)',
             'title': 'Dustbin may be full or Air Duct is blocked. Clean it'},
      'es': {'content': 'Vacía el depósito de @val y retira cualquier posible obstrucción en el '
                        'compactador de polvo y la cámara. (36)',
             'title': 'Es posible que el depósito esté lleno o haya residuos bloqueando la cámara'},
      'fr': {'content': 'Videz le bac de @val et éliminez toute obstruction possible du compacteur '
                        'de poussière et du conduit d’aspiration. (36)',
             'title': 'Le bac est peut-être plein ou des débris bloquent le conduit d’aspiration'},
      'it': {'content': 'Svuotare il cestino di @val e rimuovere eventuali ostruzioni dal '
                        'compattatore della polvere e dal condotto. (36)',
             'title': 'Il cestino potrebbe essere pieno o della sporcizia potrebbe bloccare il '
                      'condotto'},
      'nl': {'content': 'Leeg de opvangbak van @val en verwijder eventuele verstoppingen uit de '
                        'stofpers en het plenum. (36)',
             'title': 'Opvangbak kan vol zijn of vuil kan het plenum blokkeren'},
      'pl': {'content': 'Opróżnij pojemnik robota @val i usuń wszelkie możliwe blokady w '
                        'zgniatarce kurzu oraz kanale powietrznym. (36)',
             'title': 'Pojemnik może być pełny, a zanieczyszczenia mogą blokować kanał powietrzny'},
      'pt': {'content': 'Esvazie o depósito de @val e remova quaisquer obstruções do compactador '
                        'de pó e do conduto. (36)',
             'title': 'O depósito pode estar cheio ou ter resíduos a bloquear o conduto'}},
 42: {'de': {'content': 'Öffnen Sie Türen und entfernen Sie Hindernisse, die den Weg blockieren '
                        'könnten, und versuchen Sie es erneut. (42)',
             'title': '@val konnte einen Ihrer Räume nicht erreichen'},
      'en': {'content': 'Open doors and clear obstacles that could be blocking its path and try '
                        'again. (42)',
             'title': '@val\xa0couldn’t reach one of your rooms'},
      'es': {'content': 'Abre las puertas, retira los obstáculos que puedan estar bloqueando su '
                        'camino e inténtalo de nuevo. (42)',
             'title': '@val no ha podido llegar a una de las habitaciones'},
      'fr': {'content': 'Ouvrez les portes, dégagez les obstacles qui pourraient bloquer son '
                        'chemin et réessayez. (42)',
             'title': '@val n’a pas pu atteindre l’une de vos pièces'},
      'it': {'content': 'Aprire le porte, rimuovere gli ostacoli che potrebbero bloccare il '
                        'percorso e riprovare. (42)',
             'title': '@val non è riuscito a raggiungere una delle stanze'},
      'nl': {'content': 'Open deuren en verwijder obstakels die het pad kunnen blokkeren en '
                        'probeer het opnieuw. (42)',
             'title': '@val kon een van uw kamers niet bereiken'},
      'pl': {'content': 'Otwórz drzwi i usuń przeszkody, które mogą blokować drogę, a następnie '
                        'spróbuj ponownie. (42)',
             'title': 'Robot @val nie mógł dotrzeć do jednego z pomieszczeń'},
      'pt': {'content': 'Abra portas e remova obstáculos que possam estar a bloquear o caminho e '
                        'tente novamente. (42)',
             'title': '@val não conseguiu chegar a uma das divisões'}},
 44: {'de': {'content': 'Bitte lesen Sie unseren Hilfeartikel für Schritte zur Behebung dieses '
                        'Problems durch. (44)',
             'title': 'Pumpe im Wasserbehälter des Roboters ist möglicherweise blockiert'},
      'en': {'content': 'Please view our help article for steps to troubleshoot this issue. (44)',
             'title': 'Robot water bin pump may be blocked'},
      'es': {'content': 'Consulta nuestro artículo de ayuda para conocer los pasos para solucionar '
                        'este problema. (44)',
             'title': 'Es posible que la bomba del depósito de agua del robot esté bloqueada'},
      'fr': {'content': 'Veuillez consulter notre article d’aide pour connaître les étapes de '
                        'dépannage de ce problème. (44)',
             'title': 'La pompe du bac d’eau du robot est peut-être bloquée'},
      'it': {'content': 'Consultare il nostro articolo della guida per i passaggi su come '
                        'risolvere questo problema. (44)',
             'title': "La pompa del serbatoio dell'acqua del robot potrebbe essere bloccata"},
      'nl': {'content': 'Raadpleeg ons help-artikel voor stappen om dit probleem op te lossen. '
                        '(44)',
             'title': 'De pomp van de watertank van de robot is mogelijk verstopt'},
      'pl': {'content': 'Zapoznaj się z artykułem pomocy, by dowiedzieć się, jak rozwiązać ten '
                        'problem. (44)',
             'title': 'Pompa zbiornika na wodę robota może być zablokowana'},
      'pt': {'content': 'Consulte o nosso artigo de ajuda para ver os passos de resolução deste '
                        'problema. (44)',
             'title': 'A bomba do depósito de água do robô pode estar bloqueada'}},
 46: {'de': {'content': 'Stellen Sie @val auf seine Dockingstation und lassen Sie ihn ausreichend '
                        'aufladen. Sie können den Akkustatus hier auf der Registerkarte "Roboter" '
                        'überprüfen. (46)',
             'title': 'Akkustand zu niedrig für die Reinigung'},
      'en': {'content': 'Place\xa0@val\xa0on its dock and allow it to charge sufficiently. You can '
                        'check battery status here in the Robots tab. (46)',
             'title': 'Battery too low to clean'},
      'es': {'content': 'Coloca @val en su base y deja que cargue lo suficiente. Puedes comprobar '
                        'el estado de la batería aquí, en la pestaña Robots. (46)',
             'title': 'Batería demasiado baja para limpiar'},
      'fr': {'content': 'Placez @val sur sa station d’accueil et laissez-le se recharger '
                        'suffisamment. Vous pouvez vérifier l’état de la batterie ici, dans '
                        'l’onglet Robots. (46)',
             'title': 'Batterie trop faible pour nettoyer'},
      'it': {'content': 'Posizionare @val sulla sua base e lasciarlo caricare a un livello '
                        'sufficiente. È possibile controllare lo stato della batteria qui, nella '
                        'scheda Robot. (46)',
             'title': 'Batteria troppo scarica per la pulizia'},
      'nl': {'content': 'Plaats @val op het dock en laat het voldoende opladen. Je kunt de '
                        'batterijstatus hier controleren op het tabblad Robots. (46)',
             'title': 'Batterij is te zwak om te reinigen'},
      'pl': {'content': 'Umieść robota @val na stacji dokującej i pozwól mu się wystarczająco '
                        'naładować. Stan naładowania akumulatora możesz sprawdzić tutaj, na '
                        'zakładce Roboty. (46)',
             'title': 'Zbyt niski poziom akumulatora, aby sprzątać'},
      'pt': {'content': 'Coloque @val na base e permita que carregue suficientemente. Pode '
                        'verificar o estado da bateria aqui no separador Robôs. (46)',
             'title': 'Bateria demasiado fraca para limpar'}},
 47: {'de': {'content': 'Gehen Sie im unteren App-Menü zur Registerkarte "Support" und wenden Sie '
                        'sich an unser Team, damit wir Ihren Roboter per Fernzugriff aktualisieren '
                        'können.\n'
                        'Dadurch wird ein Sensor aktualisiert, der zur ordnungsgemäßen Funktion '
                        'von @val beiträgt. (47)',
             'title': 'Wichtiges Update verfügbar – wir unterstützen Sie dabei'},
      'en': {'content': 'Go to the Support tab from the bottom app menu and contact our team so we '
                        'can remotely update your robot.\n'
                        'This will update a sensor that helps\xa0@val\xa0work properly. (47)',
             'title': 'Important update available – we’re here to help'},
      'es': {'content': 'Ve a la pestaña Atención al cliente en el menú inferior de la app y '
                        'contacta con nuestro equipo para que podamos actualizar tu robot de forma '
                        'remota.\n'
                        'Se actualizará un sensor que contribuye a que @val funcione '
                        'correctamente. (47)',
             'title': 'Actualización importante disponible: estamos aquí para ayudarte'},
      'fr': {'content': 'Accédez à l’onglet Assistance dans le menu inférieur de l’application et '
                        'contactez notre équipe pour que nous puissions mettre à jour votre robot '
                        'à distance.\n'
                        'Cela mettra à jour un capteur qui aide @val à fonctionner correctement. '
                        '(47)',
             'title': 'Mise à jour importante disponible ; nous sommes là pour vous aider'},
      'it': {'content': "Accedere alla scheda Assistenza dal menu in basso dell'app e contattare "
                        'il nostro team, in modo da poter aggiornare da remoto il robot.\n'
                        'Questo aggiornerà un sensore che aiuta @val a funzionare correttamente. '
                        '(47)',
             'title': 'Aggiornamento importante disponibile – siamo qui per aiutarti'},
      'nl': {'content': 'Ga naar de tab Ondersteuning in het onderste menu van de app en neem '
                        'contact op met ons team, zodat we je robot op afstand kunnen updaten.\n'
                        'Hiermee wordt een sensor bijgewerkt die ervoor zorgt dat @val correct '
                        'werkt. (47)',
             'title': 'Er is een belangrijke update beschikbaar – we helpen je graag'},
      'pl': {'content': 'Przejdź do karty Wsparcie w dolnym menu aplikacji i skontaktuj się z '
                        'naszym zespołem, abyśmy mogli zdalnie zaktualizować robota.\n'
                        'Zaktualizuje to czujnik, który umożliwia robotowi @val prawidłowe '
                        'działanie. (47)',
             'title': 'Dostępna jest ważna aktualizacja — chętnie pomożemy'},
      'pt': {'content': 'Vá ao separador Suporte no menu inferior da aplicação e contacte a nossa '
                        'equipa para que possamos atualizar remotamente o seu robô.\n'
                        'Isto irá atualizar um sensor que ajuda @val a funcionar corretamente. '
                        '(47)',
             'title': 'Atualização importante disponível – estamos aqui para ajudar'}},
 48: {'de': {'content': 'Stellen Sie sicher, dass Türen offen und frei von Hindernissen sind. Es '
                        'sollte auch überprüft werden, ob Ihre Karte Ihren Raum präzise abbildet. '
                        '(48)',
             'title': 'Ein Hindernis blockierte den Eingang zu einem Raum'},
      'en': {'content': 'Make sure doors are open and free from obstacles. It’s also a good idea '
                        'to check that your map accurately represents your space. (48)',
             'title': 'An obstacle blocked the entrance to a room'},
      'es': {'content': 'Asegúrate de que las puertas estén abiertas y libres de obstáculos. '
                        'También es recomendable comprobar que el mapa represente el espacio con '
                        'exactitud. (48)',
             'title': 'Un obstáculo bloqueaba la entrada a una habitación'},
      'fr': {'content': 'Assurez-vous que les portes sont ouvertes et dégagées de tout obstacle. '
                        'Il est également recommandé de vérifier que votre carte représente '
                        'fidèlement votre espace. (48)',
             'title': 'Un obstacle a bloqué l’entrée d’une pièce'},
      'it': {'content': 'Assicurarsi che le porte siano aperte e libere da ostacoli. È anche una '
                        'buona idea verificare che la mappa rappresenti accuratamente lo spazio. '
                        '(48)',
             'title': "Un ostacolo bloccava l'ingresso a una stanza"},
      'nl': {'content': 'Zorg ervoor dat deuren open zijn en vrij van obstakels. Het is ook een '
                        'goed idee om te controleren of je kaart je ruimte accuraat weergeeft. '
                        '(48)',
             'title': 'Een obstakel blokkeerde de ingang van een kamer'},
      'pl': {'content': 'Upewnij się, że drzwi są otwarte i wolne od przeszkód. Warto również '
                        'sprawdzić, czy mapa dokładnie odzwierciedla przestrzeń. (48)',
             'title': 'Przeszkoda zablokowała wejście do pomieszczenia'},
      'pt': {'content': 'Certifique-se de que as portas estão abertas e sem obstáculos. Também é '
                        'recomendável verificar se o mapa representa corretamente o seu espaço. '
                        '(48)',
             'title': 'Um obstáculo bloqueou a entrada de uma divisão'}},
 66: {'de': {'content': 'Nehmen Sie für einen Neustart den Roboter von der Dockingstation, halten '
                        'Sie die Ein-/Aus-Taste zum Ausschalten 10 Sekunden lang gedrückt und '
                        'halten Sie sie dann zum Einschalten erneut 3 Sekunden lang gedrückt. (66)',
             'title': 'Für den Speicher ist ein kurzer Neustart erforderlich'},
      'en': {'content': 'To reboot, remove from dock, press and hold Power button for 10 seconds '
                        'to Power off, press and hold again for 3 seconds to Power back on. (66）',
             'title': 'Memory storage needs a quick reboot'},
      'es': {'content': 'Para reiniciar, retira el robot de la base, mantén pulsado el botón de '
                        'encendido durante 10\xa0segundos para apagarlo y vuelve a mantenerlo '
                        'pulsado durante 3\xa0segundos para encenderlo de nuevo. (66)',
             'title': 'Es necesario un reinicio rápido del almacenamiento en memoria'},
      'fr': {'content': 'Pour redémarrer, retirez de la station d’accueil, maintenez le bouton '
                        'd’alimentation enfoncé pendant 10 secondes pour éteindre, puis appuyez de '
                        'nouveau et maintenez-le enfoncé pendant 3 secondes pour rallumer. (66)',
             'title': 'Le stockage en mémoire nécessite un redémarrage rapide'},
      'it': {'content': 'Per riavviare, rimuovere il robot dalla base, tenere premuto il pulsante '
                        'di accensione per 10 secondi per spegnere, quindi tenere premuto di nuovo '
                        'per 3 secondi per riaccendere. (66)',
             'title': 'La memoria richiede un riavvio rapido'},
      'nl': {'content': 'Om opnieuw op te starten, haal het apparaat van het dock, houd de '
                        'aan-/uitknop 10 seconden ingedrukt om uit te schakelen en houd deze '
                        'opnieuw 3 seconden ingedrukt om weer in te schakelen. (66)',
             'title': 'Geheugenopslag heeft een snelle herstart nodig'},
      'pl': {'content': 'Aby ponownie uruchomić robota, zdejmij go ze stacji dokującej, naciśnij i '
                        'przytrzymaj przycisk zasilania przez 10\xa0sekund, aby go wyłączyć, a '
                        'następnie ponownie naciśnij i przytrzymaj przez 3\xa0sekundy, aby go '
                        'włączyć. (66)',
             'title': 'Pamięć wymaga szybkiego ponownego uruchomienia'},
      'pt': {'content': 'Para reiniciar, retire da base, prima sem soltar o botão de alimentação '
                        'durante 10 segundos para desligar, prima novamente durante 3 segundos '
                        'para ligar. (66)',
             'title': 'A memória precisa de um reinício rápido'}},
 68: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Entfernen Sie ihn von der '
                        'Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden lang '
                        'gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (68)',
             'title': 'Kamera kann Objekte und Hindernisse nicht erkennen'},
      'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, hold '
                        'the Power button for 10s then 3s. (68)',
             'title': 'Camera unable to detect objects and obstacles'},
      'es': {'content': 'Reinicia @val para solucionar el error. Retíralo de la base y mantén '
                        'pulsado el botón de encendido durante 10\xa0segundos. Luego mantenlo '
                        'presionado 3s. (68)',
             'title': 'La cámara no puede detectar objetos ni obstáculos'},
      'fr': {'content': 'Redémarrez @val pour effacer l’erreur. Retirez-le de la station '
                        'd’accueil, puis maintenez le bouton d’alimentation enfoncé pendant 10 '
                        'secondes. Puis maintenez-le enfoncé pendant 3s. (68)',
             'title': 'La caméra ne parvient pas à détecter les objets et les obstacles'},
      'it': {'content': "Riavviare @val per risolvere l'errore. Rimuovere dalla base, quindi "
                        'tenere premuto il pulsante di accensione per 10 secondi. Quindi tienilo '
                        'premuto per 3s. (68)',
             'title': 'Fotocamera non in grado di rilevare oggetti e ostacoli'},
      'nl': {'content': 'Start @val opnieuw op om de fout te wissen. Verwijder het van het '
                        'basisstation en houd de aan/uit-knop 10 seconden ingedrukt. Houd deze '
                        'daarna 3s ingedrukt. (68)',
             'title': 'Camera kan geen objecten en obstakels detecteren'},
      'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia błędu. Wyjmij ze stacji '
                        'dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania przez '
                        '10\xa0sekund. Następnie przytrzymaj przez 3s. (68)',
             'title': 'Kamera nie może wykryć obiektów i przeszkód'},
      'pt': {'content': 'Reinicie @val para corrigir o erro. Retire da base e depois prima sem '
                        'soltar o botão de alimentação durante 10 segundos. Em seguida, mantenha '
                        'premido por 3s. (68)',
             'title': 'A câmara não consegue detetar objetos e obstáculos'}},
 69: {'de': {'content': 'Achten Sie darauf, dass Türen geöffnet sind und der Pfad zur '
                        'Dockingstation nicht blockiert ist. Stellen Sie @val auf die '
                        'Dockingstation, wenn der Akku leer ist. (69)',
             'title': '@val konnte den Weg zurück nicht finden'},
      'en': {'content': 'Make sure doors are open and that nothing is blocking the path to the '
                        'dock. Place\xa0@val\xa0on dock if its battery has run out. (69)',
             'title': '@val\xa0was unable to find its way home'},
      'es': {'content': 'Asegúrate de que las puertas estén abiertas y de que no haya nada '
                        'bloqueando el camino a la base. Si @val se ha quedado sin batería, '
                        'colócalo en la base. (69)',
             'title': '@val no ha podido encontrar el camino de vuelta a la base'},
      'fr': {'content': 'Assurez-vous que les portes sont ouvertes et que rien ne bloque le '
                        'passage vers la station d’accueil. Placez @val sur la station d’accueil '
                        'si sa batterie est épuisée. (69)',
             'title': '@val n’a pas pu retrouver son chemin vers la station d’accueil'},
      'it': {'content': 'Assicurarsi che le porte siano aperte e che non ci siano ostacoli lungo '
                        'il percorso verso la base. Posizionare @val sulla base se la batteria è '
                        'esaurita. (69)',
             'title': '@val non è riuscito a tornare alla base'},
      'nl': {'content': 'Zorg ervoor dat de deuren open zijn en dat niets het pad naar het dock '
                        'blokkeert. Plaats @val op het dock als de accu leeg is. (69)',
             'title': '@val kon de weg naar huis niet vinden'},
      'pl': {'content': 'Upewnij się, że drzwi są otwarte i nic nie blokuje drogi do stacji '
                        'dokującej. Umieść robota @val w stacji dokującej, jeśli jego akumulator '
                        'się wyczerpał. (69)',
             'title': 'Robot @val nie mógł znaleźć drogi powrotnej'},
      'pt': {'content': 'Certifique-se de que as portas estão abertas e que nada bloqueia o '
                        'caminho até à base. Coloque @val na base se a bateria tiver acabado. (69)',
             'title': '@val não conseguiu encontrar o caminho de regresso à base'}},
 101: {'de': {'content': '@val hat Probleme, seinen Akku zu erkennen. Entfernen Sie den Akku und '
                         'setzen Sie ihn wieder ein, um den Fehler zu beheben. (101)',
              'title': 'Ladeproblem: Akku nicht erkannt'},
       'en': {'content': '@val\xa0is having trouble detecting its battery. Remove and reinstall '
                         'battery to clear. (101)',
              'title': 'Charging Issue: battery not detected'},
       'es': {'content': '@val tiene problemas para detectar la batería. Retira y vuelve a '
                         'instalar la batería para solucionarlo. (101)',
              'title': 'Problema de carga: batería no detectada'},
       'fr': {'content': '@val n’arrive pas à détecter sa batterie. Retirez puis réinstallez la '
                         'batterie pour effacer l’erreur. (101)',
              'title': 'Problème de chargement : batterie non détectée'},
       'it': {'content': '@val ha problemi a rilevare la batteria. Rimuovere e reinstallare la '
                         'batteria per risolvere il problema. (101)',
              'title': 'Problema di ricarica: batteria non rilevata'},
       'nl': {'content': '@val heeft problemen met het detecteren van de accu. Verwijder de accu '
                         'en plaats deze opnieuw om de fout te wissen. (101)',
              'title': 'Oplaadprobleem: batterij niet gedetecteerd'},
       'pl': {'content': 'Robot @val ma problem z wykryciem akumulatora. Wyjmij i włóż ponownie '
                         'akumulator, aby usunąć błąd. (101)',
              'title': 'Problem z ładowaniem: nie wykryto akumulatora'},
       'pt': {'content': '@val está com dificuldade em detetar a bateria. Remova e volte a '
                         'instalar a bateria para corrigir. (101)',
              'title': 'Problema de carregamento: bateria não detetada'}},
 102: {'de': {'content': '@val hat Probleme, seinen Akku zu erkennen. Entfernen Sie den Akku und '
                         'setzen Sie ihn wieder ein, um den Fehler zu beheben. (102)',
              'title': 'Ladeproblem: Aufladen nicht möglich'},
       'en': {'content': '@val\xa0is having trouble detecting its battery. Remove and reinstall '
                         'battery to clear. (102)',
              'title': 'Charging Issue: unable to charge'},
       'es': {'content': '@val tiene problemas para detectar la batería. Retira y vuelve a '
                         'instalar la batería para solucionarlo. (102)',
              'title': 'Problema de carga: no se puede cargar'},
       'fr': {'content': '@val n’arrive pas à détecter sa batterie. Retirez puis réinstallez la '
                         'batterie pour effacer l’erreur. (102)',
              'title': 'Problème de chargement : impossible de recharger'},
       'it': {'content': '@val ha problemi a rilevare la batteria. Rimuovere e reinstallare la '
                         'batteria per risolvere il problema. (102)',
              'title': 'Problema di ricarica: impossibile ricaricare'},
       'nl': {'content': '@val heeft problemen met het detecteren van de accu. Verwijder de accu '
                         'en plaats deze opnieuw om te wissen. (102)',
              'title': 'Oplaadprobleem: kan niet worden opgeladen'},
       'pl': {'content': 'Robot @val ma problem z wykryciem akumulatora. Wyjmij i włóż ponownie '
                         'akumulator, aby usunąć błąd. (102)',
              'title': 'Problem z ładowaniem: nie można naładować'},
       'pt': {'content': '@val está com dificuldade em detetar a bateria. Remova e volte a '
                         'instalar a bateria para corrigir. (102)',
              'title': 'Problema de carregamento: não é possível carregar'}},
 103: {'de': {'content': '@val hat Probleme, seinen Akku zu erkennen. Entfernen Sie den Akku und '
                         'setzen Sie ihn wieder ein, um den Fehler zu beheben. (103)',
              'title': 'Ladeproblem: Aufladen nicht möglich'},
       'en': {'content': '@val\xa0is having trouble detecting its battery. Remove and reinstall '
                         'battery to clear. (103)',
              'title': 'Charging Issue: unable to charge'},
       'es': {'content': '@val tiene problemas para detectar la batería. Retira y vuelve a '
                         'instalar la batería para solucionarlo. (103)',
              'title': 'Problema de carga: no se puede cargar'},
       'fr': {'content': '@val n’arrive pas à détecter sa batterie. Retirez puis réinstallez la '
                         'batterie pour effacer l’erreur. (103)',
              'title': 'Problème de chargement : impossible de recharger'},
       'it': {'content': '@val ha problemi a rilevare la batteria. Rimuovere e reinstallare la '
                         'batteria per risolvere il problema. (103)',
              'title': 'Problema di ricarica: impossibile ricaricare'},
       'nl': {'content': '@val heeft problemen met het detecteren van de accu. Verwijder de accu '
                         'en plaats deze opnieuw om te wissen. (103)',
              'title': 'Oplaadprobleem: kan niet worden opgeladen'},
       'pl': {'content': 'Robot @val ma problem z wykryciem akumulatora. Wyjmij i włóż ponownie '
                         'akumulator, aby usunąć błąd. (103)',
              'title': 'Problem z ładowaniem: nie można naładować'},
       'pt': {'content': '@val está com dificuldade em detetar a bateria. Remova e volte a '
                         'instalar a bateria para corrigir. (103)',
              'title': 'Problema de carregamento: não é possível carregar'}},
 104: {'de': {'content': 'Trennen Sie die Dockingstation vom Strom und wischen Sie die '
                         'Ladekontakte am Roboter und an der Dockingstation mit einem leicht '
                         'feuchten Tuch ab. (104)',
              'title': 'Ladeproblem: Kontakte müssen gereinigt werden'},
       'en': {'content': 'Unplug the Dock Power, then wipe the Charging Contacts on Robot and Dock '
                         'with a slightly damp tissue. (104)',
              'title': 'Charging Issue: contacts need to be cleaned'},
       'es': {'content': 'Desenchufa la base y limpia los contactos de carga del robot y de la '
                         'base con un paño ligeramente húmedo. (104)',
              'title': 'Problema de carga: es necesario limpiar los contactos'},
       'fr': {'content': 'Débranchez l’alimentation de la station d’accueil, puis essuyez les '
                         'contacts de charge du robot et de la station avec un chiffon légèrement '
                         'humide. (104)',
              'title': 'Problème de chargement : les contacts doivent être nettoyés'},
       'it': {'content': 'Scollega la base dall’alimentazione e pulisci i contatti di ricarica del '
                         'robot e della base con un panno leggermente umido. (104)',
              'title': 'Problema di ricarica: è necessario ripulire i contatti'},
       'nl': {'content': 'Haal de stekker van het basisstation uit het stopcontact en veeg de '
                         'laadcontacten van de robot en het basisstation schoon met een licht '
                         'vochtige doek. (104)',
              'title': 'Oplaadprobleem: contacten moeten gereinigd worden'},
       'pl': {'content': 'Odłącz zasilanie stacji dokującej, a następnie przetrzyj styki ładowania '
                         'robota i stacji dokującej lekko wilgotną ściereczką. (104)',
              'title': 'Problem z ładowaniem: styki wymagają wyczyszczenia'},
       'pt': {'content': 'Desligue a base da alimentação e limpe os contactos de carregamento do '
                         'robô e da base com um pano ligeiramente húmido. (104)',
              'title': 'Problema de carregamento: contactos precisam de limpeza'}},
 105: {'de': {'content': 'Trennen Sie die Dockingstation vom Strom und wischen Sie die '
                         'Ladekontakte am Roboter und an der Dockingstation mit einem leicht '
                         'feuchten Tuch ab. (105)',
              'title': 'Ladeproblem: Kontakte müssen gereinigt werden'},
       'en': {'content': 'Unplug the Dock Power, then wipe the Charging Contacts on Robot and Dock '
                         'with a slightly damp tissue. (105)',
              'title': 'Charging Issue: contacts need to be cleaned'},
       'es': {'content': 'Desenchufa la base y limpia los contactos de carga del robot y de la '
                         'base con un paño ligeramente húmedo. (105)',
              'title': 'Problema de carga: es necesario limpiar los contactos'},
       'fr': {'content': 'Débranchez l’alimentation de la station d’accueil, puis essuyez les '
                         'contacts de charge du robot et de la station avec un chiffon légèrement '
                         'humide. (105)',
              'title': 'Problème de chargement : les contacts doivent être nettoyés'},
       'it': {'content': 'Scollega la base dall’alimentazione e pulisci i contatti di ricarica del '
                         'robot e della base con un panno leggermente umido. (105)',
              'title': 'Problema di ricarica: è necessario ripulire i contatti'},
       'nl': {'content': 'Haal de stekker van het basisstation uit het stopcontact en veeg de '
                         'laadcontacten van de robot en het basisstation schoon met een licht '
                         'vochtige doek. (105)',
              'title': 'Oplaadprobleem: contacten moeten gereinigd worden'},
       'pl': {'content': 'Odłącz zasilanie stacji dokującej, a następnie przetrzyj styki ładowania '
                         'robota i stacji dokującej lekko wilgotną ściereczką. (105)',
              'title': 'Problem z ładowaniem: styki wymagają wyczyszczenia'},
       'pt': {'content': 'Desligue a base da alimentação e limpe os contactos de carregamento do '
                         'robô e da base com um pano ligeiramente húmido. (105)',
              'title': 'Problema de carregamento: contactos precisam de limpeza'}},
 106: {'de': {'content': 'Stellen Sie sicher, dass @val und Dockingstation bei Raumtemperatur '
                         'aufbewahrt werden. Entfernen Sie sie von jeglichen Wärmequellen. (106)',
              'title': 'Ladeproblem: Warten Sie, bis der Akku abgekühlt ist, und versuchen Sie es '
                       'erneut'},
       'en': {'content': 'Make sure\xa0@val\xa0and dock are stored in a room temperature location. '
                         'Move away from heat source. (106)',
              'title': 'Charging Issue: Wait for the battery to cool down and try again'},
       'es': {'content': 'Asegúrate de que @val y la base se encuentren a temperatura ambiente. '
                         'Aléjalos de fuentes de calor. (106)',
              'title': 'Problema de carga: espera a que la batería se enfríe e inténtalo de nuevo'},
       'fr': {'content': 'Assurez-vous que @val et la station d’accueil se trouvent dans un '
                         'endroit à température ambiante. Éloigner de toute source de chaleur. '
                         '(106)',
              'title': 'Problème de charge : attendez que la batterie refroidisse'},
       'it': {'content': 'Assicurarsi che @val e la base si trovino a temperatura ambiente. '
                         'Allontanare da fonti di calore. (106)',
              'title': 'Problema di ricarica: attendi che la batteria si raffreddi e riprova'},
       'nl': {'content': 'Zorg ervoor dat de @val en het dock zich in een ruimte op '
                         'kamertemperatuur bevinden. Plaats uit de buurt van een warmtebron. (106)',
              'title': 'Oplaadprobleem: wacht tot de accu is afgekoeld en probeer het opnieuw'},
       'pl': {'content': 'Upewnij się, że robot @val i stacja dokująca są przechowywane w '
                         'temperaturze pokojowej. Odsuń od źródła ciepła. (106)',
              'title': 'Problem z ładowaniem: Poczekaj, aż akumulator ostygnie, i spróbuj '
                       'ponownie'},
       'pt': {'content': 'Certifique-se de que @val e a base estão num local à temperatura '
                         'ambiente. Afaste-os de fontes de calor. (106)',
              'title': 'Problema de carregamento: aguarde que a bateria arrefeça e tente '
                       'novamente'}},
 107: {'de': {'content': 'Stellen Sie sicher, dass @val und Dockingstation bei Raumtemperatur '
                         'aufbewahrt werden. Entfernen Sie sie von jeglichen Wärmequellen. (107)',
              'title': 'Ladeproblem: Warten Sie, bis der Akku abgekühlt ist, und versuchen Sie es '
                       'erneut'},
       'en': {'content': 'Make sure\xa0@val\xa0and Dock are stored in a room temperature location. '
                         'Move away from heat source. (107)',
              'title': 'Charging Issue: Wait for the battery to cool down and try again'},
       'es': {'content': 'Asegúrate de que @val y la base se encuentren a temperatura ambiente. '
                         'Aléjalos de fuentes de calor. (107)',
              'title': 'Problema de carga: espera a que la batería se enfríe e inténtalo de nuevo'},
       'fr': {'content': 'Assurez-vous que @val et la station d’accueil se trouvent dans un '
                         'endroit à température ambiante. Éloignez de toute source de chaleur. '
                         '(107)',
              'title': 'Problème de charge : attendez que la batterie refroidisse'},
       'it': {'content': 'Assicurarsi che @val e la base si trovino a temperatura ambiente. '
                         'Allontanare da fonti di calore. (107)',
              'title': 'Problema di ricarica: attendi che la batteria si raffreddi e riprova'},
       'nl': {'content': 'Zorg ervoor dat de @val en het basisstation zich in een ruimte op '
                         'kamertemperatuur bevinden. Verplaats het weg van warmtebronnen. (107)',
              'title': 'Oplaadprobleem: wacht tot de accu is afgekoeld en probeer het opnieuw'},
       'pl': {'content': 'Upewnij się, że robot @val i stacja dokująca są przechowywane w '
                         'temperaturze pokojowej. Odsuń od źródła ciepła. (107)',
              'title': 'Problem z ładowaniem: Poczekaj, aż akumulator ostygnie, i spróbuj '
                       'ponownie'},
       'pt': {'content': 'Certifique-se de que @val e a base estão num local à temperatura '
                         'ambiente. Afaste-os de fontes de calor. (107)',
              'title': 'Problema de carregamento: aguarde que a bateria arrefeça e tente '
                       'novamente'}},
 109: {'de': {'content': '@val hat Probleme, seinen Akku zu erkennen. Entfernen Sie den Akku, '
                         'warten Sie 15 Minuten und setzen Sie ihn zur Fehlerbehebung wieder ein. '
                         '(109)',
              'title': 'Ladeproblem: Aufladen nicht möglich'},
       'en': {'content': '@val\xa0is having trouble detecting its battery. Remove battery, wait 15 '
                         'minutes, and reinstall to clear. (109)',
              'title': 'Charging Issue: unable to charge'},
       'es': {'content': '@val tiene problemas para detectar la batería. Retira la batería, espera '
                         '15\xa0minutos y vuelve a instalarla para solucionarlo. (109)',
              'title': 'Problema de carga: no se puede cargar'},
       'fr': {'content': '@val n’arrive pas à détecter sa batterie. Retirez la batterie, patientez '
                         '15 minutes, puis réinstallez-la pour effacer l’erreur. (109)',
              'title': 'Problème de chargement : impossible de recharger'},
       'it': {'content': '@val ha problemi a rilevare la batteria. Rimuovere la batteria, '
                         'attendere 15 minuti e reinstallarla per ripristinare. (109)',
              'title': 'Problema di ricarica: impossibile ricaricare'},
       'nl': {'content': '@val heeft problemen met het detecteren van de accu. Verwijder de accu, '
                         'wacht 15 minuten en plaats deze opnieuw om de fout te wissen. (109)',
              'title': 'Oplaadprobleem: kan niet worden opgeladen'},
       'pl': {'content': 'Robot @val ma problem z wykryciem akumulatora. Wyjmij akumulator, '
                         'odczekaj 15\xa0minut i włóż go ponownie, aby usunąć błąd. (109)',
              'title': 'Problem z ładowaniem: nie można naładować'},
       'pt': {'content': '@val está com dificuldade em detetar a bateria. Remova a bateria, '
                         'aguarde 15 minutos e volte a instalar para corrigir. (109)',
              'title': 'Problema de carregamento: não é possível carregar'}},
 110: {'de': {'content': 'Bitte ersetzen Sie den Akku von @val. Stellen Sie sicher, dass Sie einen '
                         'originalen Akku von iRobot für Ihr Robotermodell verwenden. (110)',
              'title': 'Ladeproblem: Wenden Sie sich zum Austausch des Akkus an den Kundenservice'},
       'en': {'content': 'Please replace\xa0@val’s battery. Make sure you use an authentic iRobot '
                         'battery for your robot model. (110)',
              'title': 'Charging Issue: Contact customer service to replace the battery'},
       'es': {'content': 'Sustituye la batería de @val. Asegúrate de usar una batería iRobot '
                         'auténtica adecuada para tu modelo de robot. (110)',
              'title': 'Problema de carga: contacta con atención al cliente para sustituir la '
                       'batería'},
       'fr': {'content': 'Veuillez remplacer la batterie de @val. Assurez-vous d’utiliser une '
                         'batterie iRobot authentique pour votre modèle de robot. (110)',
              'title': 'Problème de charge : contactez le service client pour remplacer la '
                       'batterie'},
       'it': {'content': 'Sostituire la batteria di @val. Assicurarsi di utilizzare una batteria '
                         'iRobot originale per il proprio modello di robot. (110)',
              'title': 'Problema di ricarica: contatta il servizio clienti per sostituire la '
                       'batteria'},
       'nl': {'content': 'Vervang de batterij van @val. Zorg ervoor dat u een originele '
                         'iRobot-accu voor uw robotmodel gebruikt. (110)',
              'title': 'Oplaadprobleem: neem contact op met de klantenservice om de accu te '
                       'vervangen'},
       'pl': {'content': 'Wymień akumulator robota @val. Upewnij się, że używasz oryginalnego '
                         'akumulatora iRobot odpowiedniego dla modelu robota. (110)',
              'title': 'Problem z ładowaniem: Skontaktuj się z obsługą klienta w celu wymiany '
                       'akumulatora'},
       'pt': {'content': 'Substitua a bateria de @val. Certifique-se de que utiliza uma bateria '
                         'iRobot original para o seu modelo de robô. (110)',
              'title': 'Problema de carregamento: contacte o apoio ao cliente para substituir a '
                       'bateria'}},
 111: {'de': {'content': '@val hat Probleme, seinen Akku zu erkennen. Entfernen Sie den Akku, '
                         'warten Sie 15 Minuten und setzen Sie ihn zur Fehlerbehebung wieder ein. '
                         '(111)',
              'title': 'Ladeproblem: Aufladen nicht möglich'},
       'en': {'content': '@val\xa0is having trouble detecting its battery. Remove battery, wait 15 '
                         'minutes, and reinstall to clear. (111)',
              'title': 'Charging Issue: unable to charge'},
       'es': {'content': '@val tiene problemas para detectar la batería. Retira la batería, espera '
                         '15\xa0minutos y vuelve a instalarla para solucionarlo. (111)',
              'title': 'Problema de carga: no se puede cargar'},
       'fr': {'content': '@val n’arrive pas à détecter sa batterie. Retirez la batterie, patientez '
                         '15 minutes, puis réinstallez-la pour effacer l’erreur. (111)',
              'title': 'Problème de chargement : impossible de recharger'},
       'it': {'content': '@val ha problemi a rilevare la batteria. Rimuovere la batteria, '
                         'attendere 15 minuti e reinstallarla per ripristinare. (111)',
              'title': 'Problema di ricarica: impossibile ricaricare'},
       'nl': {'content': '@val heeft problemen met het detecteren van de accu. Verwijder de accu, '
                         'wacht 15 minuten en plaats deze opnieuw om te wissen. (111)',
              'title': 'Oplaadprobleem: kan niet worden opgeladen'},
       'pl': {'content': 'Robot @val ma problem z wykryciem akumulatora. Wyjmij akumulator, '
                         'odczekaj 15\xa0minut i włóż go ponownie, aby usunąć błąd. (111)',
              'title': 'Problem z ładowaniem: nie można naładować'},
       'pt': {'content': '@val está com dificuldade em detetar a bateria. Remova a bateria, '
                         'aguarde 15 minutos e volte a instalar para corrigir. (111)',
              'title': 'Problema de carregamento: não é possível carregar'}},
 114: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Entfernen Sie ihn von '
                         'der Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden '
                         'lang gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (114)',
              'title': 'Ladeproblem'},
       'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, '
                         'hold the Power button for 10s then 3s. (114)',
              'title': 'Charging Issue'},
       'es': {'content': 'Reinicia @val para solucionar el error. Retíralo de la base y mantén '
                         'pulsado el botón de encendido durante 10\xa0segundos. Luego mantenlo '
                         'presionado 3s. (114)',
              'title': 'Problema de carga'},
       'fr': {'content': 'Redémarrez @val pour effacer l’erreur. Retirez-le de la station '
                         'd’accueil, puis maintenez le bouton d’alimentation enfoncé pendant 10 '
                         'secondes. Puis maintenez-le enfoncé pendant 3s. (114)',
              'title': 'Problème de charge'},
       'it': {'content': "Riavviare @val per risolvere l'errore. Rimuovere dalla base, quindi "
                         'tenere premuto il pulsante di accensione per 10 secondi. Quindi tienilo '
                         'premuto per 3s. (114)',
              'title': 'Problema di ricarica'},
       'nl': {'content': 'Start @val opnieuw op om de fout te wissen. Verwijder het van het '
                         'basisstation en houd de aan/uit-knop 10 seconden ingedrukt. Houd deze '
                         'daarna 3s ingedrukt. (114)',
              'title': 'Oplaadprobleem'},
       'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia błędu. Wyjmij ze stacji '
                         'dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania przez '
                         '10\xa0sekund. Następnie przytrzymaj przez 3s. (114)',
              'title': 'Błąd ładowania'},
       'pt': {'content': 'Reinicie @val para corrigir o erro. Retire da base e depois prima sem '
                         'soltar o botão de alimentação durante 10 segundos. Em seguida, mantenha '
                         'premido por 3s. (114)',
              'title': 'Problema de carregamento'}},
 115: {'de': {'content': 'Bitte ersetzen Sie den Akku von @val. Stellen Sie sicher, dass Sie einen '
                         'originalen Akku von iRobot für Ihr Robotermodell verwenden. (115)',
              'title': 'Ladeproblem: Wenden Sie sich zum Austausch des Akkus an den Kundenservice'},
       'en': {'content': 'Please replace\xa0@val’s battery. Make sure you use an authentic iRobot '
                         'battery for your robot model. (115)',
              'title': 'Charging Issue: Contact customer service to replace the battery'},
       'es': {'content': 'Sustituye la batería de @val. Asegúrate de usar una batería iRobot '
                         'auténtica adecuada para tu modelo de robot. (115)',
              'title': 'Problema de carga: contacta con atención al cliente para sustituir la '
                       'batería'},
       'fr': {'content': 'Veuillez remplacer la batterie de @val. Assurez-vous d’utiliser une '
                         'batterie iRobot authentique pour votre modèle de robot. (115)',
              'title': 'Problème de charge : contactez le service client pour remplacer la '
                       'batterie'},
       'it': {'content': 'Sostituire la batteria di @val. Assicurarsi di utilizzare una batteria '
                         'iRobot originale per il proprio modello di robot. (115)',
              'title': 'Problema di ricarica: contatta il servizio clienti per sostituire la '
                       'batteria'},
       'nl': {'content': 'Vervang de batterij van @val. Zorg ervoor dat u een originele '
                         'iRobot-accu voor uw robotmodel gebruikt. (115)',
              'title': 'Oplaadprobleem: neem contact op met de klantenservice om de accu te '
                       'vervangen'},
       'pl': {'content': 'Wymień akumulator robota @val. Upewnij się, że używasz oryginalnego '
                         'akumulatora iRobot odpowiedniego dla modelu robota. (115)',
              'title': 'Problem z ładowaniem: Skontaktuj się z obsługą klienta w celu wymiany '
                       'akumulatora'},
       'pt': {'content': 'Substitua a bateria de @val. Certifique-se de que utiliza uma bateria '
                         'iRobot original para o seu modelo de robô. (115)',
              'title': 'Problema de carregamento: contacte o apoio ao cliente para substituir a '
                       'bateria'}},
 117: {'de': {'content': '@val hat Probleme, seinen Akku zu erkennen. Entfernen Sie den Akku, '
                         'warten Sie 15 Minuten und setzen Sie ihn zur Fehlerbehebung wieder ein. '
                         '(117)',
              'title': 'Ladeproblem: Aufladen nicht möglich'},
       'en': {'content': '@val\xa0is having trouble detecting its battery. Remove battery, wait 15 '
                         'minutes, and reinstall to clear. (117)',
              'title': 'Charging Issue: unable to charge'},
       'es': {'content': '@val tiene problemas para detectar la batería. Retira la batería, espera '
                         '15\xa0minutos y vuelve a instalarla para solucionarlo. (117)',
              'title': 'Problema de carga: no se puede cargar'},
       'fr': {'content': '@val n’arrive pas à détecter sa batterie. Retirez la batterie, patientez '
                         '15 minutes, puis réinstallez-la pour effacer l’erreur. (117)',
              'title': 'Problème de chargement : impossible de recharger'},
       'it': {'content': '@val ha problemi a rilevare la batteria. Rimuovere la batteria, '
                         'attendere 15 minuti e reinstallarla per ripristinare. (117)',
              'title': 'Problema di ricarica: impossibile ricaricare'},
       'nl': {'content': '@val heeft problemen met het detecteren van de accu. Verwijder de accu, '
                         'wacht 15 minuten en plaats deze opnieuw om te wissen. (117)',
              'title': 'Oplaadprobleem: kan niet worden opgeladen'},
       'pl': {'content': 'Robot @val ma problem z wykryciem akumulatora. Wyjmij akumulator, '
                         'odczekaj 15\xa0minut i włóż go ponownie, aby usunąć błąd. (117)',
              'title': 'Problem z ładowaniem: nie można naładować'},
       'pt': {'content': '@val está com dificuldade em detetar a bateria. Remova a bateria, '
                         'aguarde 15 minutos e volte a instalar para corrigir. (117)',
              'title': 'Problema de carregamento: não é possível carregar'}},
 119: {'de': {'content': 'Stecken Sie die Dockingstation vom Stromnetz aus und reinigen Sie die '
                         'Ladekontakte an Roboter und Dockingstation mit einem feuchten '
                         'Schmutzradierer. (119)',
              'title': 'Ladeproblem: Kontakte müssen gereinigt werden'},
       'en': {'content': 'Unplug the Dock, then wipe the Charging Contacts on Robot and Dock with '
                         'a slightly damp tissue. (119)',
              'title': 'Charging Issue: contacts need to be cleaned'},
       'es': {'content': 'Desenchufa la base y limpia los contactos de carga del robot y de la '
                         'base con un pañuelo ligeramente húmedo. (119)',
              'title': 'Problema de carga: es necesario limpiar los contactos'},
       'fr': {'content': 'Débranchez la station d’accueil, puis essuyez les contacts de chargement '
                         'du robot et de la station d’accueil avec un mouchoir légèrement humide. '
                         '(119)',
              'title': 'Problème de chargement : les contacts doivent être nettoyés'},
       'it': {'content': 'Scollegare la base, quindi pulire i contatti di ricarica sul robot e '
                         'sulla base con un fazzoletto leggermente umido. (119)',
              'title': 'Problema di ricarica: è necessario ripulire i contatti'},
       'nl': {'content': 'Haal de stekker van het basisstation uit het stopcontact en veeg de '
                         'oplaadcontacten op de robot en het basisstation schoon met een licht '
                         'vochtig doekje. (119)',
              'title': 'Oplaadprobleem: contacten moeten gereinigd worden'},
       'pl': {'content': 'Odłącz stację dokującą, a następnie przetrzyj styki ładowania robota i '
                         'stacji dokującej lekko wilgotną ściereczką. (119)',
              'title': 'Problem z ładowaniem: styki wymagają wyczyszczenia'},
       'pt': {'content': 'Desligue a base e limpe os contactos de carregamento no robô e na base '
                         'com um lenço ligeiramente húmido. (119)',
              'title': 'Problema de carregamento: contactos precisam de limpeza'}},
 120: {'de': {'content': '@val hat Probleme, seinen Akku zu erkennen. Entfernen Sie den Akku, '
                         'warten Sie 15 Minuten und setzen Sie ihn zur Fehlerbehebung wieder ein. '
                         '(120)',
              'title': 'Ladeproblem: Aufladen nicht möglich'},
       'en': {'content': '@val\xa0is having trouble detecting its battery. Remove battery, wait 15 '
                         'minutes, and reinstall to clear. (120)',
              'title': 'Charging Issue: unable to charge'},
       'es': {'content': '@val tiene problemas para detectar la batería. Retira la batería, espera '
                         '15\xa0minutos y vuelve a instalarla para solucionarlo. (120)',
              'title': 'Problema de carga: no se puede cargar'},
       'fr': {'content': '@val n’arrive pas à détecter sa batterie. Retirez la batterie, patientez '
                         '15 minutes, puis réinstallez-la pour effacer l’erreur. (120)',
              'title': 'Problème de chargement : impossible de recharger'},
       'it': {'content': '@val ha problemi a rilevare la batteria. Rimuovere la batteria, '
                         'attendere 15 minuti e reinstallarla per ripristinare. (120)',
              'title': 'Problema di ricarica: impossibile ricaricare'},
       'nl': {'content': '@val heeft problemen met het detecteren van de accu. Verwijder de accu, '
                         'wacht 15 minuten en plaats deze opnieuw om te wissen. (120)',
              'title': 'Oplaadprobleem: kan niet worden opgeladen'},
       'pl': {'content': 'Robot @val ma problem z wykryciem akumulatora. Wyjmij akumulator, '
                         'odczekaj 15\xa0minut i włóż go ponownie, aby usunąć błąd. (120)',
              'title': 'Problem z ładowaniem: nie można naładować'},
       'pt': {'content': '@val está com dificuldade em detetar a bateria. Remova a bateria, '
                         'aguarde 15 minutos e volte a instalar para corrigir. (120)',
              'title': 'Problema de carregamento: não é possível carregar'}},
 121: {'de': {'content': 'Stecken Sie die Dockingstation vom Stromnetz aus und reinigen Sie die '
                         'Ladekontakte an Roboter und Dockingstation mit einem feuchten '
                         'Schmutzradierer. (121)',
              'title': 'Ladeproblem: Kontakte müssen gereinigt werden'},
       'en': {'content': 'Unplug the Dock, then wipe the Charging Contacts on Robot and Dock with '
                         'a slightly damp tissue. (121)',
              'title': 'Charging Issue: contacts need to be cleaned'},
       'es': {'content': 'Desenchufa la base y limpia los contactos de carga del robot y de la '
                         'base con un pañuelo ligeramente húmedo. (121)',
              'title': 'Problema de carga: es necesario limpiar los contactos'},
       'fr': {'content': 'Débranchez la station d’accueil, puis essuyez les contacts de chargement '
                         'du robot et de la station d’accueil avec un mouchoir légèrement humide. '
                         '(121)',
              'title': 'Problème de chargement : les contacts doivent être nettoyés'},
       'it': {'content': 'Scollegare la base, quindi pulire i contatti di ricarica sul robot e '
                         'sulla base con un fazzoletto leggermente umido. (121)',
              'title': 'Problema di ricarica: è necessario ripulire i contatti'},
       'nl': {'content': 'Haal de stekker van het basisstation uit het stopcontact en veeg de '
                         'oplaadcontacten op de robot en het basisstation schoon met een licht '
                         'vochtig doekje. (121)',
              'title': 'Oplaadprobleem: contacten moeten gereinigd worden'},
       'pl': {'content': 'Odłącz stację dokującą, a następnie przetrzyj styki ładowania robota i '
                         'stacji dokującej lekko wilgotną ściereczką. (121)',
              'title': 'Problem z ładowaniem: styki wymagają wyczyszczenia'},
       'pt': {'content': 'Desligue a base e limpe os contactos de carregamento no robô e na base '
                         'com um lenço ligeiramente húmido. (121)',
              'title': 'Problema de carregamento: contactos precisam de limpeza'}},
 201: {'de': {'content': 'Bewegen Sie @val an einen anderen Ort und versuchen Sie es erneut. (201)',
              'title': 'Start nicht möglich: Treppe oder Absturzstelle erkannt'},
       'en': {'content': 'Please move\xa0@val\xa0to a new location and try again. (201)',
              'title': 'Unable to start: stairs or drop-off detected'},
       'es': {'content': 'Mueve @val a una nueva ubicación e inténtalo de nuevo. (201)',
              'title': 'No se puede iniciar: se han detectado escalones o un desnivel'},
       'fr': {'content': 'Veuillez déplacer @val vers un nouvel emplacement et réessayer. (201)',
              'title': 'Impossible de démarrer : escaliers ou vide détectés'},
       'it': {'content': 'Spostare @val in una nuova posizione e riprovare. (201)',
              'title': 'Impossibile avviare: scale o dislivelli rilevati'},
       'nl': {'content': 'Verplaats @val naar een nieuwe locatie en probeer het opnieuw. (201)',
              'title': 'Kan niet starten: trappen of afstapje gedetecteerd'},
       'pl': {'content': 'Przenieś robota @val w nowe miejsce i spróbuj ponownie. (201)',
              'title': 'Nie można rozpocząć: wykryto schody lub spadek'},
       'pt': {'content': 'Mova @val para outro local e tente novamente. (201)',
              'title': 'Não é possível iniciar: escadas ou queda detetada'}},
 202: {'de': {'content': 'Der Roboter hat erkannt, dass er in der Luft schwebt. Bitte bringen Sie '
                         'ihn an einen neuen Ort und starten Sie ihn erneut.',
              'title': 'Roboter schwebt in der Luft'},
       'en': {'content': 'The Robot has detected that it is suspended in mid-air. Please move it '
                         'to a new location and start again.',
              'title': 'Robot is suspended'},
       'es': {'content': 'El robot ha detectado que está suspendido en el aire. Muévelo a una '
                         'nueva ubicación e inícialo de nuevo.',
              'title': 'Robot suspendido'},
       'fr': {'content': 'Le robot a détecté qu’il est en suspension dans les airs. Veuillez le '
                         'déplacer vers un nouvel emplacement et le redémarrer.',
              'title': 'Robot en suspension'},
       'it': {'content': 'Il robot ha rilevato di essere sospeso in aria. Spostalo in una nuova '
                         'posizione e riavvialo.',
              'title': 'Robot sospeso in aria'},
       'nl': {'content': 'De robot heeft gedetecteerd dat hij in de lucht zweeft. Verplaats hem '
                         'naar een nieuwe locatie en start opnieuw.',
              'title': 'Robot zweeft in de lucht'},
       'pl': {'content': 'Robot wykrył, że jest zawieszony w powietrzu. Przenieś go w nowe miejsce '
                         'i uruchom ponownie.',
              'title': 'Robot jest zawieszony'},
       'pt': {'content': 'O robô detetou que está suspenso no ar. Mova-o para um novo local e '
                         'inicie novamente.',
              'title': 'Robô suspenso no ar'}},
 207: {'de': {'content': 'Bitte setzen Sie den Behälter von @val ein und versuchen Sie es erneut. '
                         '(207)',
              'title': 'Start nicht möglich: Behälter nicht installiert'},
       'en': {'content': 'Please install\xa0@val’s bin and try again. (207)',
              'title': 'Unable to start: bin not installed'},
       'es': {'content': 'Instala el depósito de @val e inténtalo de nuevo. (207)',
              'title': 'No se puede iniciar: depósito de polvo no instalado'},
       'fr': {'content': 'Veuillez installer le bac de @val et réessayer. (207)',
              'title': 'Impossible de démarrer : bac non installé'},
       'it': {'content': 'Installare il cestino di @val e riprovare. (207)',
              'title': 'Impossibile avviare: cestino non installato'},
       'nl': {'content': 'Installeer de opvangbak van @val en probeer het opnieuw. (207)',
              'title': 'Kan niet starten: opvangbak niet geïnstalleerd'},
       'pl': {'content': 'Zamontuj pojemnik robota @val i spróbuj ponownie. (207)',
              'title': 'Nie można rozpocząć: pojemnik nie jest zamontowany'},
       'pt': {'content': 'Instale o depósito de @val e tente novamente. (207)',
              'title': 'Não é possível iniciar: depósito não instalado'}},
 210: {'de': {'content': 'Bewegen Sie @val aus der nicht zu befahrenden Zone heraus, damit der '
                         'Roboter seine neue Routine starten kann. (210)',
              'title': 'Start nicht möglich: hängt in einer nicht zu befahrenden Zone fest'},
       'en': {'content': 'Move\xa0@val\xa0out of the Keep Out Zone so it can start its new '
                         'routine. (210)',
              'title': 'Unable to start: stuck in a Keep Out Zone'},
       'es': {'content': 'Mueve @val fuera de la zona de exclusión para que pueda iniciar la nueva '
                         'rutina. (210)',
              'title': 'No se puede iniciar: robot atascado en una zona de exclusión'},
       'fr': {'content': 'Déplacez @val en dehors de la zone à ignorer pour qu’il puisse démarrer '
                         'sa nouvelle routine. (210)',
              'title': 'Impossible de démarrer : bloqué dans une zone à ignorer'},
       'it': {'content': 'Spostare @val fuori dalla zona da escludere per poter avviare la nuova '
                         'routine. (210)',
              'title': 'Impossibile avviare: bloccato in una zona da escludere'},
       'nl': {'content': 'Verplaats @val uit de verbodszone zodat deze de nieuwe routine kan '
                         'starten. (210)',
              'title': 'Kan niet starten: vast in een verbodszone'},
       'pl': {'content': 'Przenieś robota @val poza strefę bez dostępu, aby mógł rozpocząć nową '
                         'rutynę. (210)',
              'title': 'Nie można rozpocząć: utknął w strefie bez dostępu'},
       'pt': {'content': 'Retire @val da Zona de Exclusão para que possa iniciar a nova rotina. '
                         '(210)',
              'title': 'Não é possível iniciar: preso numa Zona de Exclusão'}},
 215: {'de': {'content': 'Bitte lassen Sie @val den Akku ausreichend aufladen und versuchen Sie es '
                         'erneut. (215)',
              'title': 'Start nicht möglich: Akkustand niedrig'},
       'en': {'content': 'Please allow\xa0@val\xa0to charge its battery sufficiently and try '
                         'again. (215)',
              'title': 'Unable to start: battery low, recharge it'},
       'es': {'content': 'Deja que @val cargue la batería lo suficiente e inténtalo de nuevo. '
                         '(215)',
              'title': 'No se puede iniciar: batería baja'},
       'fr': {'content': 'Veuillez laisser @val recharger suffisamment sa batterie et réessayer. '
                         '(215)',
              'title': 'Impossible de démarrer : batterie faible'},
       'it': {'content': 'Lasciare che @val carichi sufficientemente la batteria e riprovare. '
                         '(215)',
              'title': 'Impossibile avviare: batteria scarica'},
       'nl': {'content': 'Laat @val voldoende opladen en probeer het opnieuw. (215)',
              'title': 'Kan niet starten: accu bijna leeg'},
       'pl': {'content': 'Poczekaj, aż robot @val naładuje się wystarczająco i spróbuj ponownie. '
                         '(215)',
              'title': 'Nie można rozpocząć: niski poziom akumulatora, naładuj go'},
       'pt': {'content': 'Permita que @val carregue suficientemente a bateria e tente novamente. '
                         '(215)',
              'title': 'Não é possível iniciar: bateria fraca'}},
 216: {'de': {'content': 'Leeren Sie den Behälter von @val und entfernen Sie mögliche Hindernisse, '
                         'damit Staubverdichter und Kanal frei sind. (216)',
              'title': 'Start nicht möglich: Behälter voll oder verstopft'},
       'en': {'content': 'Empty\xa0@val’s bin and clear any possible obstructions to the dust '
                         'compactor and plenum is clear. (216)',
              'title': 'Unable to start: bin full or clogged'},
       'es': {'content': 'Vacía el depósito de @val y retira cualquier posible obstrucción '
                         'asegurándote de que el compactador de polvo y la cámara estén '
                         'despejados. (216)',
              'title': 'No se puede iniciar: depósito lleno u obstruido'},
       'fr': {'content': 'Videz le bac de @val et éliminez toute obstruction possible du '
                         'compacteur de poussière et du conduit d’aspiration. (216)',
              'title': 'Impossible de démarrer : bac plein ou bouché'},
       'it': {'content': 'Svuotare il cestino di @val e rimuovere eventuali ostruzioni dal '
                         'compattatore della polvere e assicurarsi che il condotto sia libero. '
                         '(216)',
              'title': 'Impossibile avviare: cestino pieno o ostruito'},
       'nl': {'content': 'Leeg de opvangbak van @val en verwijder eventuele verstoppingen, zodat '
                         'de stofverdichter en het plenum vrij zijn. (216)',
              'title': 'Kan niet starten: opvangbak vol of verstopt'},
       'pl': {'content': 'Opróżnij pojemnik robota @val i wyczyść wszelkie możliwe blokady w '
                         'zgniatarce kurzu oraz kanale powietrznym. (216)',
              'title': 'Nie można rozpocząć: pojemnik jest pełny lub zatkany'},
       'pt': {'content': 'Esvazie o depósito de @val e remova quaisquer obstruções do compactador '
                         'de pó e do conduto. (216)',
              'title': 'Não é possível iniciar: depósito cheio ou obstruído'}},
 218: {'de': {'content': 'Lassen Sie @val auf seiner Dockingstation, bis das Update abgeschlossen '
                         'ist. Reinigung wird in Kürze verfügbar sein. (218)',
              'title': 'Start nicht möglich: Roboter-Update läuft'},
       'en': {'content': 'Leave\xa0@val\xa0on its Dock until update is complete. Cleaning will be '
                         'available shortly. (218)',
              'title': 'Unable to start: robot update in progress'},
       'es': {'content': 'Deja @val en su base hasta que se complete la actualización. La limpieza '
                         'estará disponible en breve. (218)',
              'title': 'No se puede iniciar: actualización del robot en curso'},
       'fr': {'content': 'Laissez @val sur sa station d’accueil jusqu’à ce que la mise à jour soit '
                         'terminée. Le nettoyage sera bientôt disponible. (218)',
              'title': 'Impossible de démarrer : mise à jour du robot en cours'},
       'it': {'content': "Lasciare @val sulla base fino al completamento dell'aggiornamento. La "
                         'pulizia sarà di nuovo disponibile a breve. (218)',
              'title': 'Impossibile avviare: aggiornamento del robot in corso'},
       'nl': {'content': 'Laat @val op het dock staan tot de update is voltooid. Schoonmaken is '
                         'binnenkort beschikbaar. (218)',
              'title': 'Kan niet starten: robotupdate wordt uitgevoerd'},
       'pl': {'content': 'Pozostaw robota @val w stacji dokującej do zakończenia aktualizacji. '
                         'Sprzątanie będzie wkrótce dostępne. (218)',
              'title': 'Nie można rozpocząć: trwa aktualizacja robota'},
       'pt': {'content': 'Deixe @val na base até a atualização estar concluída. A limpeza estará '
                         'disponível em breve. (218)',
              'title': 'Não é possível iniciar: atualização do robô em curso'}},
 222: {'de': {'content': 'Bewegen Sie @val an einen anderen Ort und versuchen Sie es erneut. (222)',
              'title': 'Start nicht möglich: Problem mit dem Navigationsmodul'},
       'en': {'content': 'Move\xa0@val\xa0to a new location and try again. (222)',
              'title': 'Unable to start: Navigation Module issue, restart the Robot'},
       'es': {'content': 'Mueve @val a una nueva ubicación e inténtalo de nuevo. (222)',
              'title': 'No se puede iniciar: problema del módulo de navegación'},
       'fr': {'content': 'Déplacez @val vers un nouvel emplacement et réessayez. (222)',
              'title': 'Impossible de démarrer : problème du module de navigation'},
       'it': {'content': 'Spostare @val in una nuova posizione e riprovare. (222)',
              'title': 'Impossibile avviare: problema del modulo di navigazione'},
       'nl': {'content': 'Verplaats @val naar een nieuwe locatie en probeer het opnieuw. (222)',
              'title': 'Kan niet starten: probleem met navigatiemodule'},
       'pl': {'content': 'Przenieś robota @val w nowe miejsce i spróbuj ponownie. (222)',
              'title': 'Nie można rozpocząć: problem z modułem nawigacji, uruchom ponownie robota'},
       'pt': {'content': 'Mova @val para outro local e tente novamente. (222)',
              'title': 'Não é possível iniciar: problema no módulo de navegação'}},
 224: {'de': {'content': 'Überprüfen Sie, ob die Karte von @val präzise ist, und versuchen Sie es '
                         'erneut. (224)',
              'title': 'Start nicht möglich: Kartenproblem'},
       'en': {'content': "Check that\xa0@val's map is accurate and try again. (224)",
              'title': 'Unable to start: Map issue, please remap'},
       'es': {'content': 'Comprueba que el mapa de @val sea correcto e inténtalo de nuevo. (224)',
              'title': 'No se puede iniciar: problema con el mapa'},
       'fr': {'content': 'Vérifiez que la carte de @val est correcte et réessayez. (224)',
              'title': 'Impossible de démarrer : problème de carte'},
       'it': {'content': 'Verificare che la mappa di @val sia accurata e riprovare. (224)',
              'title': 'Impossibile avviare: problema della mappa'},
       'nl': {'content': 'Controleer of de kaart van @val nauwkeurig is en probeer het opnieuw. '
                         '(224)',
              'title': 'Kan niet starten: kaartprobleem'},
       'pl': {'content': 'Sprawdź, czy mapa robota @val jest dokładna i spróbuj ponownie. (224)',
              'title': 'Nie można rozpocząć: problem z mapą, wykonaj mapowanie ponownie'},
       'pt': {'content': 'Verifique se o mapa de @val está correto e tente novamente. (224)',
              'title': 'Não é possível iniciar: problema no mapa'}},
 228: {'de': {'content': 'Gehen Sie im unteren App-Menü zur Registerkarte "Support" und wenden Sie '
                         'sich an unser Team, damit wir Ihren Roboter per Fernzugriff '
                         'aktualisieren können.\n'
                         'Dadurch wird ein Sensor aktualisiert, der zur ordnungsgemäßen Funktion '
                         'von @val beiträgt. (228)',
              'title': 'Start nicht möglich: Wichtiges Update verfügbar'},
       'en': {'content': 'Go to the Support tab from the bottom app menu and contact our team so '
                         'we can remotely update your robot.\n'
                         'This will update a sensor that helps\xa0@val\xa0work properly. (228)',
              'title': 'Unable to start: Update to the latest version'},
       'es': {'content': 'Ve a la pestaña Atención al cliente en el menú inferior de la app y '
                         'contacta con nuestro equipo para que podamos actualizar tu robot de '
                         'forma remota.\n'
                         'Se actualizará un sensor que contribuye a que @val funcione '
                         'correctamente. (228)',
              'title': 'No se puede iniciar: Actualización importante disponible'},
       'fr': {'content': 'Accédez à l’onglet Assistance dans le menu inférieur de l’application et '
                         'contactez notre équipe pour que nous puissions mettre à jour votre robot '
                         'à distance.\n'
                         'Cela mettra à jour un capteur qui aide @val à fonctionner correctement. '
                         '(228)',
              'title': 'Impossible de démarrer : Mise à jour importante disponible'},
       'it': {'content': "Accedere alla scheda Assistenza dal menu in basso dell'app e contattare "
                         'il nostro team, in modo da poter aggiornare da remoto il robot.\n'
                         'Questo aggiornerà un sensore che aiuta @val a funzionare correttamente. '
                         '(228)',
              'title': 'Impossibile avviare: Importante aggiornamento disponibile'},
       'nl': {'content': 'Ga naar de tab ondersteuning in het onderste menu van de app en neem '
                         'contact op met ons team, zodat we je robot op afstand kunnen updaten.\n'
                         'Hiermee wordt een sensor bijgewerkt die ervoor zorgt dat @val correct '
                         'werkt. (228)',
              'title': 'Kan niet starten: Belangrijke update beschikbaar'},
       'pl': {'content': 'Przejdź do karty Wsparcie w dolnym menu aplikacji i skontaktuj się z '
                         'naszym zespołem, abyśmy mogli zdalnie zaktualizować robota.\n'
                         'Zaktualizuje to czujnik, który umożliwia robotowi @val prawidłowe '
                         'działanie. (228)',
              'title': 'Nie można rozpocząć: Dostępna jest ważna aktualizacja'},
       'pt': {'content': 'Vá ao separador Suporte no menu inferior da aplicação e contacte a nossa '
                         'equipa para que possamos atualizar remotamente o seu robô.\n'
                         'Isto irá atualizar um sensor que ajuda @val a funcionar corretamente. '
                         '(228)',
              'title': 'Não é possível iniciar: Atualização importante disponível'}},
 231: {'de': {'content': 'Bitte füllen Sie den Dockingstation-Tank vollständig auf und versuchen '
                         'Sie es erneut. (231)',
              'title': 'Start nicht möglich: Frischwassertankstand niedrig'},
       'en': {'content': 'Fill up the Clean Water Tank and try again. (231)',
              'title': 'Unable to start: Clean Water Tank level low'},
       'es': {'content': 'Llena el tanque de la base por completo e inténtalo de nuevo. (231)',
              'title': 'No se puede iniciar: nivel bajo del depósito de agua limpia'},
       'fr': {'content': 'Veuillez remplir complètement le réservoir de la station d’accueil et '
                         'réessayer. (231)',
              'title': 'Impossible de démarrer : niveau bas du réservoir d’eau propre'},
       'it': {'content': 'Riempire completamente il serbatoio della base e riprovare. (231)',
              'title': 'Impossibile avviare: livello basso del serbatoio dell’acqua pulita'},
       'nl': {'content': 'Vul de tank van het basisstation volledig bij en probeer het opnieuw. '
                         '(231)',
              'title': 'Kan niet starten: schoonwatertank bijna leeg'},
       'pl': {'content': 'Całkowicie napełnij zbiornik na czystą wodę i spróbuj ponownie. (231)',
              'title': 'Nie można rozpocząć: niski poziom w zbiorniku na czystą wodę'},
       'pt': {'content': 'Encha completamente o depósito da base e tente novamente. (231)',
              'title': 'Não é possível iniciar: nível baixo do depósito de água limpa'}},
 234: {'de': {'content': 'Bitte befestigen Sie einen Mopp und versuchen Sie es erneut. (234)',
              'title': 'Start nicht möglich: kein Mopp angebracht'},
       'en': {'content': 'Please attach a mop and try again. (234)',
              'title': 'Unable to start: no mop attached'},
       'es': {'content': 'Instala una mopa e inténtalo de nuevo. (234)',
              'title': 'No se puede iniciar: mopa no instalada'},
       'fr': {'content': 'Veuillez fixer une serpillière et réessayer. (234)',
              'title': 'Impossible de démarrer : aucune serpillière fixée'},
       'it': {'content': 'Installare un panno di lavaggio e riprovare. (234)',
              'title': 'Impossibile avviare: panno di lavaggio non installato'},
       'nl': {'content': 'Bevestig een dweil en probeer het opnieuw. (234)',
              'title': 'Kan niet starten: geen dweil bevestigd'},
       'pl': {'content': 'Załóż nakładkę mopującą i spróbuj ponownie. (234)',
              'title': 'Nie można rozpocząć: nie zamontowano mopa'},
       'pt': {'content': 'Instale uma mopa e tente novamente. (234)',
              'title': 'Não é possível iniciar: mopa não instalada'}},
 237: {'de': {'content': 'Bitte setzen Sie den Akku von @val ein und versuchen Sie es erneut. '
                         '(237)',
              'title': 'Start nicht möglich: kein Akku erkannt'},
       'en': {'content': "Please install\xa0@val's battery and try again. (237)",
              'title': 'Unable to start: no battery detected'},
       'es': {'content': 'Instala la batería de @val e inténtalo de nuevo. (237)',
              'title': 'No se puede iniciar: no se ha detectado la batería'},
       'fr': {'content': 'Veuillez installer la batterie de @val et réessayer. (237)',
              'title': 'Impossible de démarrer : aucune batterie détectée'},
       'it': {'content': 'Installare la batteria di @val e riprovare. (237)',
              'title': 'Impossibile avviare: nessuna batteria rilevata'},
       'nl': {'content': 'Installeer de batterij van @val en probeer het opnieuw. (237)',
              'title': 'Kan niet starten: geen batterij gedetecteerd'},
       'pl': {'content': 'Zamontuj akumulator robota @val i spróbuj ponownie. (237)',
              'title': 'Nie można rozpocząć: nie wykryto akumulatora'},
       'pt': {'content': 'Instale a bateria de @val e tente novamente. (237)',
              'title': 'Não é possível iniciar: bateria não detetada'}},
 238: {'de': {'content': 'Bitte setzen Sie den Akku von @val ein und versuchen Sie es erneut. '
                         '(238)',
              'title': 'Start nicht möglich: kein Akku erkannt'},
       'en': {'content': "Please install\xa0@val's battery and try again. (238)",
              'title': 'Unable to start: no battery detected'},
       'es': {'content': 'Instala la batería de @val e inténtalo de nuevo. (238)',
              'title': 'No se puede iniciar: no se ha detectado la batería'},
       'fr': {'content': 'Veuillez installer la batterie de @val et réessayer. (238)',
              'title': 'Impossible de démarrer : aucune batterie détectée'},
       'it': {'content': 'Installare la batteria di @val e riprovare. (238)',
              'title': 'Impossibile avviare: nessuna batteria rilevata'},
       'nl': {'content': 'Installeer de batterij van @val en probeer het opnieuw. (238)',
              'title': 'Kan niet starten: geen batterij gedetecteerd'},
       'pl': {'content': 'Zamontuj akumulator robota @val i spróbuj ponownie. (238)',
              'title': 'Nie można rozpocząć: nie wykryto akumulatora'},
       'pt': {'content': 'Instale a bateria de @val e tente novamente. (238)',
              'title': 'Não é possível iniciar: bateria não detetada'}},
 239: {'de': {'content': 'Reinigung wird in Kürze verfügbar sein. (239)',
              'title': 'Start nicht möglich: Karte wird gespeichert'},
       'en': {'content': 'Cleaning will be available shortly. (239)',
              'title': 'Unable to start: saving map'},
       'es': {'content': 'La limpieza estará disponible en breve. (239)',
              'title': 'No se puede iniciar: guardando mapa'},
       'fr': {'content': 'Le nettoyage sera bientôt disponible. (239)',
              'title': 'Impossible de démarrer : sauvegarde de la carte'},
       'it': {'content': 'La pulizia sarà di nuovo disponibile a breve. (239)',
              'title': 'Impossibile avviare: salvataggio mappa'},
       'nl': {'content': 'Schoonmaken is binnenkort beschikbaar. (239)',
              'title': 'Kan niet starten: kaart opslaan'},
       'pl': {'content': 'Sprzątanie będzie wkrótce dostępne. (239)',
              'title': 'Nie można rozpocząć: zapisywanie mapy'},
       'pt': {'content': 'A limpeza estará disponível em breve. (239)',
              'title': 'Não é possível iniciar: guardar mapa'}},
 251: {'de': {'content': '@val kann aufgrund eines Kameraproblems nicht navigieren. Halten Sie die '
                         'Reinigungstaste 10 Sekunden lang gedrückt, um den Fehler zu beheben. '
                         '(Fehler 251)',
              'title': 'Start nicht möglich: Kameraproblem'},
       'en': {'content': '%robotName can’t navigate because of a camera issue. To clear error, '
                         'press and hold clean button for 10 seconds. (Error 251)',
              'title': 'Unable to start: camera issue'},
       'es': {'content': '@val no puede navegar debido a un problema con la cámara. Para '
                         'solucionar el error, mantén pulsado el botón CLEAN durante 10\xa0'
                         'segundos. (Error\xa0251)',
              'title': 'No se puede iniciar: problema con la cámara'},
       'fr': {'content': '@val ne peut pas naviguer en raison d’un problème de caméra. Pour '
                         'effacer l’erreur, maintenez le bouton de nettoyage enfoncé pendant 10 '
                         'secondes. (Erreur 251)',
              'title': 'Impossible de démarrer : problème de caméra'},
       'it': {'content': '@val non riesce a spostarsi a causa di un problema alla fotocamera. Per '
                         "risolvere l'errore, tenere premuto il pulsante Pulisci per 10 secondi. "
                         '(Errore 251)',
              'title': 'Impossibile avviare: problema alla fotocamera'},
       'nl': {'content': '@val kan niet navigeren vanwege een cameraprobleem. Houd de CLEAN-knop '
                         '10 seconden ingedrukt om de fout te wissen. (Fout 251)',
              'title': 'Kan niet starten: cameraprobleem'},
       'pl': {'content': 'Robot @val nie może nawigować z powodu problemu z kamerą. Aby usunąć '
                         'błąd, naciśnij i przytrzymaj przycisk czyszczenia przez 10\xa0sekund. '
                         '(Błąd 251)',
              'title': 'Nie można rozpocząć: problem z kamerą'},
       'pt': {'content': '@val não consegue navegar devido a um problema na câmara. Para corrigir '
                         'o erro, prima sem soltar o botão Clean durante 10 segundos. (Erro 251)',
              'title': 'Não é possível iniciar: problema na câmara'}},
 266: {'de': {'content': 'Bitte besuchen Sie Ihr Mitgliedschaftsportal, um die Zahlungsmethode zu '
                         'aktualisieren und den Abonnementstatus zu überprüfen. Tippen Sie unten, '
                         'um sich beim Portal anzumelden. (266)',
              'title': 'Start nicht möglich: Problem mit dem iRobot Select-Abonnement'},
       'en': {'content': 'Please visit your Membership Portal to update payment method and check '
                         'on subscription status. Tap below to login to the portal. (266)',
              'title': 'Unable to start: issue with iRobot Select subscription'},
       'es': {'content': 'Visita el portal de suscriptores para actualizar el método de pago y '
                         'comprobar el estado de la suscripción. Toca a continuación para iniciar '
                         'sesión en el portal. (266)',
              'title': 'No se puede iniciar: problema con la suscripción a iRobot\xa0Select'},
       'fr': {'content': 'Veuillez consulter votre portail d’abonnement pour mettre à jour votre '
                         'mode de paiement et vérifier l’état de votre abonnement. Appuyez '
                         'ci-dessous pour vous connecter au portail. (266)',
              'title': 'Impossible de démarrer : problème avec l’abonnement iRobot Select'},
       'it': {'content': 'Visitare il Portale di abbonamento per aggiornare il metodo di pagamento '
                         "e controllare lo stato dell'abbonamento. Toccare qui sotto per accedere "
                         'al portale. (266)',
              'title': "Impossibile avviare: problema con l'abbonamento iRobot Select"},
       'nl': {'content': 'Bezoek je ledenportaal om de betaalmethode bij te werken en de '
                         'abonnementsstatus te controleren. Tik hieronder om in te loggen op het '
                         'portaal. (266)',
              'title': 'Kan niet starten: probleem met iRobot Select-abonnement'},
       'pl': {'content': 'Odwiedź portal dla członków, aby zaktualizować metodę płatności i '
                         'sprawdzić stan subskrypcji. Kliknij poniżej, aby zalogować się do '
                         'portalu. (266)',
              'title': 'Nie można uruchomić: problem z subskrypcją iRobot Select'},
       'pt': {'content': 'Visite o seu Portal de Membros para atualizar o método de pagamento e '
                         'verificar o estado da subscrição. Toque abaixo para iniciar sessão no '
                         'portal. (266)',
              'title': 'Não é possível iniciar: problema com a subscrição iRobot Select'}},
 268: {'de': {'content': 'Reinigung wird in Kürze verfügbar sein. (268)',
              'title': 'Start nicht möglich: Karte wird gespeichert'},
       'en': {'content': 'Cleaning will be available shortly. (268)',
              'title': 'Unable to start: saving map'},
       'es': {'content': 'La limpieza estará disponible en breve. (268)',
              'title': 'No se puede iniciar: guardando mapa'},
       'fr': {'content': 'Le nettoyage sera bientôt disponible. (268)',
              'title': 'Impossible de démarrer : sauvegarde de la carte'},
       'it': {'content': 'La pulizia sarà di nuovo disponibile a breve. (268)',
              'title': 'Impossibile avviare: salvataggio mappa'},
       'nl': {'content': 'Schoonmaken is binnenkort beschikbaar. (268)',
              'title': 'Kan niet starten: kaart opslaan'},
       'pl': {'content': 'Sprzątanie będzie wkrótce dostępne. (268)',
              'title': 'Nie można rozpocząć: zapisywanie mapy'},
       'pt': {'content': 'A limpeza estará disponível em breve. (268)',
              'title': 'Não é possível iniciar: guardar mapa'}},
 283: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Entfernen Sie ihn von '
                         'der Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden '
                         'lang gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (283)',
              'title': 'Lasersensor-Problem'},
       'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, '
                         'hold the Power button for 10s then 3s. (283)',
              'title': 'Laser sensor issue'},
       'es': {'content': 'Reinicia @val para solucionar el error. Retíralo de la base y mantén '
                         'pulsado el botón de encendido durante 10\xa0segundos. Luego mantenlo '
                         'presionado 3s. (283)',
              'title': 'Problema del sensor láser'},
       'fr': {'content': 'Redémarrez @val pour effacer l’erreur. Retirez-le de la station '
                         'd’accueil, puis maintenez le bouton d’alimentation enfoncé pendant 10 '
                         'secondes. Puis maintenez-le enfoncé pendant 3s. (283)',
              'title': 'Problème de capteur laser'},
       'it': {'content': "Riavviare @val per risolvere l'errore. Rimuovere dalla base, quindi "
                         'tenere premuto il pulsante di accensione per 10 secondi. Quindi tienilo '
                         'premuto per 3s. (283)',
              'title': 'Problema al sensore laser'},
       'nl': {'content': 'Start @val opnieuw op om de fout te wissen. Verwijder het van het '
                         'basisstation en houd de aan/uit-knop 10 seconden ingedrukt. Houd deze '
                         'daarna 3s ingedrukt. (283)',
              'title': 'Probleem met lasersensor'},
       'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia błędu. Wyjmij ze stacji '
                         'dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania przez '
                         '10\xa0sekund. Następnie przytrzymaj przez 3s. (283)',
              'title': 'Problem z czujnikiem laserowym'},
       'pt': {'content': 'Reinicie @val para corrigir o erro. Retire da base e depois prima sem '
                         'soltar o botão de alimentação durante 10 segundos. Em seguida, mantenha '
                         'premido por 3s. (283)',
              'title': 'Problema no sensor laser'}},
 284: {'de': {'content': 'Bitte löschen Sie die aktuelle Karte von @val und senden Sie den Roboter '
                         'los, um über die Registerkarte "Mein Zuhause" eine neue Karte zu '
                         'erstellen. (284)',
              'title': 'Inkompatible Karte'},
       'en': {'content': "Please delete\xa0@val's current map and send it to create a new map from "
                         'the My Home tab. (284)',
              'title': 'Map Incompatible'},
       'es': {'content': 'Elimina el mapa actual de @val y envíalo a crear uno nuevo desde la '
                         'pestaña Mi casa. (284)',
              'title': 'Mapa incompatible'},
       'fr': {'content': 'Veuillez supprimer la carte actuelle de @val et ordonnez-lui de créer '
                         'une nouvelle carte à partir de l’onglet Mon domicile. (284)',
              'title': 'Carte incompatible'},
       'it': {'content': 'Eliminare la mappa attuale di @val e avviarlo per creare una nuova mappa '
                         'dalla scheda La mia casa. (284)',
              'title': 'Mappa incompatibile'},
       'nl': {'content': 'Verwijder de huidige kaart van @val en stuur hem opnieuw in om een '
                         'nieuwe kaart te maken vanaf het tabblad My Home. (284)',
              'title': 'Incompatibele kaart'},
       'pl': {'content': 'Usuń obecną mapę robota @val i wyślij go, aby utworzył nową mapę w '
                         'zakładce Mój dom. (284)',
              'title': 'Niekompatybilna mapa'},
       'pt': {'content': 'Elimine o mapa atual de @val e envie-o para criar um novo mapa a partir '
                         'do separador A minha casa. (284)',
              'title': 'Mapa incompatível'}},
 285: {'de': {'content': 'Bitte warten Sie, bis @val die Entleerung des Wassertanks abgeschlossen '
                         'hat, bevor Sie eine neue Routine starten. (285)',
              'title': 'Start nicht möglich: Wassertank des Roboters wird gerade entleert'},
       'en': {'content': 'Please wait until\xa0@val\xa0finishes draining its water tank before '
                         'beginning a new routine. (285)',
              'title': 'Unable to start: robot water tank is currently draining'},
       'es': {'content': 'Espera a que @val termine de vaciar el tanque de agua antes de empezar '
                         'una rutina nueva. (285)',
              'title': 'No se puede iniciar: el tanque de agua del robot se está vaciando'},
       'fr': {'content': 'Veuillez attendre que @val termine de vidanger son réservoir d’eau avant '
                         'de commencer une nouvelle routine. (285)',
              'title': 'Impossible de démarrer : le réservoir d’eau du robot est en cours de '
                       'vidange'},
       'it': {'content': "Attendere che @val finisca di svuotare il serbatoio dell'acqua prima di "
                         'iniziare una nuova routine. (285)',
              'title': "Impossibile avviare: il serbatoio dell'acqua del robot si sta svuotando"},
       'nl': {'content': 'Wacht tot @val klaar is met het legen van het waterreservoir voordat u '
                         'een nieuwe routine begint. (285)',
              'title': 'Kan niet starten: het watertankje van de robot wordt momenteel geleegd'},
       'pl': {'content': 'Zanim włączysz nową rutynę, poczekaj, aż robot @val zakończy opróżnianie '
                         'zbiornika na wodę. (285)',
              'title': 'Nie można rozpocząć: obecnie trwa opróżnianie zbiornika na wodę robota'},
       'pt': {'content': 'Aguarde até @val terminar de drenar o depósito de água antes de iniciar '
                         'uma nova rotina. (285)',
              'title': 'Não é possível iniciar: o depósito de água do robô está a drenar'}},
 286: {'de': {'content': '',
              'title': 'Bereit zum Reinigen? Stellen Sie sicher, dass sich @val auf dem Boden '
                       'befindet und einsatzbereit ist'},
       'en': {'content': '',
              'title': 'Ready to clean? Make sure\xa0@val\xa0is on the floor and ready to roll'},
       'es': {'content': '',
              'title': '¿Listo para limpiar? Asegúrate de que @val esté en el suelo y listo para '
                       'empezar'},
       'fr': {'content': '',
              'title': 'Prêt à nettoyer ? Assurez-vous que @val est sur le sol et prêt à l’emploi'},
       'it': {'content': '',
              'title': 'Pronto per la pulizia? Assicurarsi che @val sia sul pavimento e pronto per '
                       "l'uso"},
       'nl': {'content': '',
              'title': 'Klaar om schoon te maken? Zorg ervoor dat @val op de vloer staat en klaar '
                       'is voor gebruik'},
       'pl': {'content': '',
              'title': 'Gotowy do sprzątania? Upewnij się, że robot @val znajduje się na podłodze '
                       'i jest gotowy do pracy'},
       'pt': {'content': '',
              'title': 'Pronto para limpar? Certifique-se de que @val está no chão e pronto para '
                       'funcionar'}},
 287: {'de': {'content': 'Entfernen Sie die Wischtuchplatte von @val, damit der Roboter mit seiner '
                         'Saugroutine beginnen kann. (287)',
              'title': 'Saugen nicht möglich: Wischtuchplatte entfernen'},
       'en': {'content': 'Remove\xa0@val’s Pad Plate so it can begin its vacuuming routine. (287)',
              'title': 'Unable to vacuum: remove Pad Plate'},
       'es': {'content': 'Retira el soporte de la mopa de @val para que pueda empezar su rutina de '
                         'aspirado. (287)',
              'title': 'No se puede aspirar: retira el soporte de la mopa'},
       'fr': {'content': 'Retirez le support de lingette de @val pour qu’il puisse commencer sa '
                         'routine d’aspiration. (287)',
              'title': 'Impossible d’aspirer : retirez le support de lingette'},
       'it': {'content': 'Rimuovere la piastra del panno di @val in modo che possa iniziare la sua '
                         'routine di aspirazione. (287)',
              'title': 'Impossibile aspirare: rimuovere la piastra del panno'},
       'nl': {'content': 'Verwijder de padplaat van @val zodat de stofzuigroutine kan beginnen. '
                         '(287)',
              'title': 'Kan niet stofzuigen: verwijder de Pad Plate'},
       'pl': {'content': 'Zdejmij płytkę nakładki robota @val, aby mógł on zacząć zaplanowane '
                         'odkurzanie. (287)',
              'title': 'Nie można odkurzać: zdejmij płytkę nakładki'},
       'pt': {'content': 'Remova a placa da mopa de @val para que possa iniciar a rotina de '
                         'aspiração. (287)',
              'title': 'Não é possível aspirar: remova a placa da mopa'}},
 290: {'de': {'content': 'Befestigen Sie ein Wischtuch an der Wischtuchplatte und bringen Sie die '
                         'Wischtuchplatte an @val an, damit der Roboter zum Wischen bereit ist. '
                         '(290)',
              'title': 'Wischen kann nicht gestartet werden: Wischtuchplatte anbringen'},
       'en': {'content': 'Attach a Mop Pad to the Pad Plate, and install the Pad Plate onto\xa0'
                         "@val\xa0so it's ready to mop. (290)",
              'title': 'Unable to start mopping: attach Pad Plate'},
       'es': {'content': 'Para fregar, coloca una mopa en su soporte e instálalo en @val. (290)',
              'title': 'No se ha podido iniciar el fregado: coloca el soporte de la mopa'},
       'fr': {'content': 'Fixez une lingette de lavage au support de lingette, puis installez '
                         'celui-ci sur @val pour qu’il soit prêt à nettoyer à la serpillière. '
                         '(290)',
              'title': 'Impossible de commencer le nettoyage à la serpillière : fixez le support '
                       'de lingette'},
       'it': {'content': 'Fissare un panno per lavaggio alla piastra del panno e installare la '
                         'piastra del panno su @val in modo che sia pronto per il lavaggio. (290)',
              'title': 'Impossibile avviare il lavaggio: installare la piastra del panno'},
       'nl': {'content': 'Bevestig een dweilpad aan de padplaat en plaats de padplaat op @val '
                         'zodat deze klaar is om te dweilen. (290)',
              'title': 'Kan niet beginnen met dweilen: bevestig de padplaat'},
       'pl': {'content': 'Przymocuj nakładkę mopującą do płytki nakładki i zamontuj płytkę '
                         'nakładki w robocie @val, aby był gotowy do mycia mopem. (290)',
              'title': 'Nie można rozpocząć mycia mopem: zamocuj płytkę nakładki'},
       'pt': {'content': 'Coloque uma mopa na placa da mopa e instale a placa em @val para que '
                         'esteja pronto para lavar. (290)',
              'title': 'Não é possível iniciar a lavagem: coloque a placa da mopa'}},
 350: {'de': {'content': 'Öffnen Sie den Deckel der Dockingstation und setzen Sie einen neuen '
                         'Beutel ein, indem Sie die Karte in die Führungsschienen schieben. Setzen '
                         'Sie den Deckel wieder auf die Dockingstation auf. (350)',
              'title': 'Entleerung des Behälters nicht verfügbar: Beutel fehlt'},
       'en': {'content': 'Lift Dock Lid and install a new Dust Bag by sliding along the Guide '
                         'Rails. Place Lid back on Dock. (350)',
              'title': 'Bin empty unavailable: bag missing'},
       'es': {'content': 'Levanta la tapa de la base e instala una bolsa nueva deslizando el '
                         'cartón por las guías. Vuelve a colocar la tapa en la base. (350)',
              'title': 'Vaciado del depósito no disponible: falta la bolsa'},
       'fr': {'content': 'Soulevez le couvercle de la station d’accueil et installez un nouveau '
                         'sac en faisant glisser le carton dans les rails de guidage. Replacez le '
                         'couvercle sur la station d’accueil. (350)',
              'title': 'Vidage du bac indisponible : sac manquant'},
       'it': {'content': 'Sollevare il coperchio della base e installare un nuovo sacchetto '
                         'facendo scorrere la scheda nelle guide. Riposizionare il coperchio sulla '
                         'base. (350)',
              'title': 'Svuotamento cestino non disponibile: sacchetto mancante'},
       'nl': {'content': 'Til het deksel van het basisstation op en installeer een nieuwe zak door '
                         'de kaart in de geleiderails te schuiven. Plaats het deksel terug op het '
                         'basisstation. (350)',
              'title': 'Opvangbak legen niet beschikbaar: zak ontbreekt'},
       'pl': {'content': 'Podnieś pokrywę stacji dokującej i zainstaluj nowy worek, wsuwając kartę '
                         'w prowadnice. Umieść pokrywę z powrotem na stacji dokującej. (350)',
              'title': 'Opróżnianie pojemnika niedostępne: brak worka'},
       'pt': {'content': 'Levante a tampa da base e instale um novo saco deslizando o cartão nas '
                         'calhas. Volte a colocar a tampa na base. (350)',
              'title': 'Esvaziamento do depósito indisponível: saco em falta'}},
 353: {'de': {'content': 'Öffnen Sie den Deckel der Dockingstation und entnehmen Sie den vollen '
                         'Beutel. Setzen Sie einen neuen Beutel ein, indem Sie die Karte in die '
                         'Führungsschienen schieben. Setzen Sie den Deckel wieder auf die '
                         'Dockingstation auf. (353)',
              'title': 'Entleerung des Behälters nicht verfügbar: Beutel voll'},
       'en': {'content': 'Lift dock lid and remove the full bag. Install a new bag by sliding the '
                         'card into the guide rails. Place lid back on dock. (353)',
              'title': 'Bin empty unavailable: bag full'},
       'es': {'content': 'Levanta la tapa de la base y retira la bolsa llena. Instala una bolsa '
                         'nueva deslizando el cartón por las guías. Vuelve a colocar la tapa en la '
                         'base. (353)',
              'title': 'Vaciado del depósito no disponible: bolsa llena'},
       'fr': {'content': 'Soulevez le couvercle de la station d’accueil et retirez le sac plein. '
                         'Installez un nouveau sac en faisant glisser le carton dans les rails de '
                         'guidage. Replacez le couvercle sur la station d’accueil. (353)',
              'title': 'Vidage du bac indisponible : sac plein'},
       'it': {'content': 'Sollevare il coperchio della base e rimuovere il sacchetto pieno. '
                         'Installare un nuovo sacchetto facendo scorrere la scheda nelle guide. '
                         'Riposizionare il coperchio sulla base. (353)',
              'title': 'Svuotamento cestino non disponibile: sacchetto pieno'},
       'nl': {'content': 'Til het deksel van het dock op en verwijder de volle zak. Installeer een '
                         'nieuwe zak door de kaart in de geleiderails te schuiven. Plaats het '
                         'deksel terug op het dock. (353)',
              'title': 'Opvangbak legen niet beschikbaar: zak vol'},
       'pl': {'content': 'Podnieś pokrywę stacji dokującej i wyjmij pełny worek. Zainstaluj nowy '
                         'worek, wsuwając kartę w prowadnice. Umieść pokrywę z powrotem na stacji '
                         'dokującej. (353)',
              'title': 'Opróżnianie pojemnika niedostępne: pełny worek'},
       'pt': {'content': 'Levante a tampa da base e remova o saco cheio. Instale um novo saco '
                         'deslizando o cartão nas calhas. Volte a colocar a tampa na base. (353)',
              'title': 'Esvaziamento do depósito indisponível: saco cheio'}},
 360: {'de': {'content': 'Zeigen Sie die Schritte zur Fehlerbehebung an, um die Kommunikation '
                         'wiederherzustellen. (360)',
              'title': '@val kann nicht mit der Dockingstation kommunizieren'},
       'en': {'content': 'View troubleshooting steps to reestablish communication. (360)',
              'title': "@val\xa0can't communicate with its Dock"},
       'es': {'content': 'Consulta los pasos de resolución de problemas para restablecer la '
                         'comunicación. (360)',
              'title': '@val no puede comunicarse con la base'},
       'fr': {'content': 'Consultez les étapes de dépannage pour rétablir la communication. (360)',
              'title': '@val ne peut pas communiquer avec sa station d’accueil'},
       'it': {'content': 'Visualizzare i passaggi per la risoluzione dei problemi per ripristinare '
                         'la comunicazione. (360)',
              'title': '@val non riesce a comunicare con la sua base'},
       'nl': {'content': 'Bekijk de stappen voor probleemoplossing om de communicatie te '
                         'herstellen. (360)',
              'title': '@val kan niet communiceren met het basisstation'},
       'pl': {'content': 'Wyświetl kroki rozwiązywania problemów, aby przywrócić komunikację. '
                         '(360)',
              'title': 'Robot @val nie może nawiązać połączenia ze stacją dokującą'},
       'pt': {'content': 'Consulte os passos de resolução para restabelecer a comunicação. (360)',
              'title': '@val não consegue comunicar com a base'}},
 365: {'de': {'content': 'Es ist am besten, den Behälter nicht öfter als einmal innerhalb von 10 '
                         'Minuten über die App zu entleeren. Dadurch wird der Motor vor '
                         'Überhitzung geschützt. (365)',
              'title': 'Entleerung des Behälters nicht verfügbar: Bitte warten Sie 10 Minuten'},
       'en': {'content': 'It’s best not to empty the bin from the app more than once in a 10 '
                         'minute period. This protects the motor from overheating. (365)',
              'title': 'Bin empty unavailable: please wait 10 minutes'},
       'es': {'content': 'Es recomendable no vaciar el depósito desde la app más de una vez en un '
                         'periodo de 10 minutos. Esto protege el motor frente a '
                         'sobrecalentamientos. (365)',
              'title': 'Vaciado del depósito no disponible: espera 10\xa0minutos'},
       'fr': {'content': 'Il est préférable de ne pas vider le bac depuis l’application plus d’une '
                         'fois au cours d’une période de 10 minutes. Cela protège le moteur d’une '
                         'surchauffe. (365)',
              'title': 'Vidage du bac indisponible : veuillez patienter 10 minutes'},
       'it': {'content': "Si consiglia di non svuotare il cestino dall'app più di una volta entro "
                         'un periodo di 10 minuti. Ciò protegge il motore dal surriscaldamento. '
                         '(365)',
              'title': 'Svuotamento cestino non disponibile: attendere 10 minuti'},
       'nl': {'content': 'Het is beter om de bak via de app niet vaker dan één keer in een periode '
                         'van 10 minuten te legen. Dit beschermt de motor tegen oververhitting. '
                         '(365)',
              'title': 'Opvangbak legen niet beschikbaar: wacht 10 minuten'},
       'pl': {'content': 'Najlepiej nie opróżniać pojemnika z poziomu aplikacji częściej niż raz '
                         'na 10\xa0minut. Chroni to silnik przed przegrzaniem. (365)',
              'title': 'Opróżnianie pojemnika niedostępne: odczekaj 10\xa0minut'},
       'pt': {'content': 'Evite esvaziar o depósito a partir da aplicação mais do que uma vez num '
                         'período de 10 minutos. Isto protege o motor de sobreaquecimento. (365)',
              'title': 'Esvaziamento do depósito indisponível: aguarde 10 minutos'}},
 450: {'de': {'content': 'Installieren Sie den Tank in der Dockingstation, um Wischen und '
                         'Moppwäsche zu ermöglichen. (450)',
              'title': 'Dockingstation-Tank fehlt'},
       'en': {'content': 'Install the tank into the dock to enable mopping and mop wash. (450)',
              'title': 'Dock tank missing'},
       'es': {'content': 'Instala el tanque en la base para permitir el fregado y el lavado de la '
                         'mopa. (450)',
              'title': 'Falta el tanque de la base'},
       'fr': {'content': 'Installez le réservoir dans la station d’accueil pour activer le '
                         'nettoyage à la serpillière et le lavage de serpillière. (450)',
              'title': 'Réservoir de la station d’accueil manquant'},
       'it': {'content': 'Installare il serbatoio nella base per abilitare il lavaggio dei '
                         'pavimenti e del panno. (450)',
              'title': 'Serbatoio della base mancante'},
       'nl': {'content': 'Installeer de tank in het dock om te kunnen dweilen en de dweil te '
                         'wassen. (450)',
              'title': 'Docktank ontbreekt'},
       'pl': {'content': 'Zainstaluj zbiornik w stacji dokującej, aby umożliwić mycie mopem i '
                         'mycie mopa. (450)',
              'title': 'Brak zbiornika w stacji dokującej'},
       'pt': {'content': 'Instale o depósito na base para ativar a lavagem e a limpeza da mopa. '
                         '(450)',
              'title': 'Depósito da base em falta'}},
 451: {'de': {'content': 'Füllen Sie den Dockingstation-Tank auf, damit @val mit dem Wischen '
                         'fortfahren kann. Wenn der Fehler weiterhin besteht, starten Sie @val '
                         'neu. (451)',
              'title': 'Frischwassertankstand niedrig'},
       'en': {'content': 'Refill Clean Water Tank so\xa0@val\xa0can continue mopping. If the error '
                         'persists, restart\xa0@val. (451)',
              'title': 'Clean Water Tank level low'},
       'es': {'content': 'Llena el tanque de la base para que @val pueda seguir fregando. Si el '
                         'error persiste, reinicia @val. (451)',
              'title': 'Nivel bajo del depósito de agua limpia'},
       'fr': {'content': 'Remplissez le réservoir de la station d’accueil pour que @val puisse '
                         'continuer à nettoyer à la serpillière. Si l’erreur persiste, redémarrez '
                         '@val. (451)',
              'title': 'Niveau bas du réservoir d’eau propre'},
       'it': {'content': 'Riempire il serbatoio della base in modo che @val possa continuare il '
                         "lavaggio. Se l'errore persiste, riavviare @val. (451)",
              'title': 'Livello basso del serbatoio dell’acqua pulita'},
       'nl': {'content': 'Vul de tank van het basisstation zodat @val verder kan dweilen. Als de '
                         'fout aanhoudt, start @val dan opnieuw op. (451)',
              'title': 'Schoonwatertank bijna leeg'},
       'pl': {'content': 'Napełnij zbiornik na czystą wodę, aby robot @val mógł kontynuować mycie '
                         'mopem. Jeśli błąd będzie się powtarzał, uruchom ponownie robota @val. '
                         '(451)',
              'title': 'Niski poziom w zbiorniku na czystą wodę'},
       'pt': {'content': 'Encha o depósito da base para que @val possa continuar a lavagem. Se o '
                         'erro persistir, reinicie @val. (451)',
              'title': 'Nível baixo do depósito de água limpa'}},
 455: {'de': {'content': 'Nachfüllen von @val nicht möglich. Saugen ist weiterhin verfügbar, aber '
                         'die Pumpenhardware muss möglicherweise ausgetauscht werden (Fehler 455)',
              'title': 'Hardwareproblem mit Dockingstation-Pumpe'},
       'en': {'content': 'Unable to refill\xa0@val. Vacuuming is still available but your pump '
                         'hardware may need to be replaced (Error 455)',
              'title': 'Dock pump hardware issue'},
       'es': {'content': 'No se puede rellenar @val. El aspirado sigue estando disponible, pero es '
                         'posible que se deba reemplazar la maquinaria de la bomba (Error 455)',
              'title': 'Problema mecánico en la bomba de la base'},
       'fr': {'content': 'Impossible de remplir @val. L’aspiration est toujours disponible, mais '
                         'le matériel de votre pompe doit peut-être être remplacé (Erreur 455)',
              'title': 'Problème matériel de la pompe de la station d’accueil'},
       'it': {'content': "Impossibile riempire @val. L'aspirazione è ancora disponibile ma "
                         "potrebbe essere necessario sostituire l'hardware della pompa (Errore "
                         '455)',
              'title': 'Problema hardware della pompa della base'},
       'nl': {'content': 'Kan @val niet bijvullen. Stofzuigen is nog steeds beschikbaar, maar uw '
                         'pomphardware moet mogelijk worden vervangen (fout 455)',
              'title': 'Hardwareprobleem met de dockpomp'},
       'pl': {'content': 'Nie można napełnić robota @val. Odkurzanie jest nadal dostępne, ale '
                         'pompa może wymagać wymiany (błąd 455)',
              'title': 'Problem sprzętowy z pompą stacji dokującej'},
       'pt': {'content': 'Não é possível reabastecer @val. A aspiração continua disponível, mas o '
                         'hardware da bomba pode precisar de ser substituído (Erro 455)',
              'title': 'Problema de hardware da bomba da base'}},
 457: {'de': {'content': 'Nachfüllen von @val nicht möglich. Stecken Sie die Dockingstation vom '
                         'Stromnetz aus und reinigen Sie die Ladekontakte an Roboter und '
                         'Dockingstation mit einem feuchten Schmutzradierer. (457)',
              'title': 'Kommunikationsproblem mit der Dockingstation'},
       'en': {'content': 'Unable to refill\xa0@val. Unplug dock and use a damp melamine sponge to '
                         'scrub charging contacts on robot and dock. (457)',
              'title': 'Dock communication issue'},
       'es': {'content': 'No se puede rellenar @val. Desenchufa la base y utiliza una esponja de '
                         'melamina húmeda para limpiar los contactos de carga del robot y de la '
                         'base. (457)',
              'title': 'Problema de comunicación con la base'},
       'fr': {'content': 'Impossible de remplir @val. Débranchez la station d’accueil et utilisez '
                         'une éponge en mélamine légèrement humide pour essuyer les contacts de '
                         'chargement du robot et de la station d’accueil. (457)',
              'title': 'Problème de communication avec la station d’accueil'},
       'it': {'content': 'Impossibile riempire @val. Scollegare la base e usare una spugna '
                         'melaminica inumidita per strofinare i contatti di ricarica sul robot e '
                         'sulla base. (457)',
              'title': 'Problema di comunicazione della base'},
       'nl': {'content': 'Kan @val niet bijvullen. Haal de stekker van het dock uit het '
                         'stopcontact en gebruik een vochtige melaminespons om de oplaadcontacten '
                         'op de robot en het dock schoon te schrobben. (457)',
              'title': 'Communicatieprobleem met dock'},
       'pl': {'content': 'Nie można napełnić robota @val. Odłącz stację dokującą i użyj wilgotnej '
                         'gąbki z melaminy, aby wyczyścić styki ładowania na robocie i stacji '
                         'dokującej. (457)',
              'title': 'Problem z komunikacją ze stacją dokującą'},
       'pt': {'content': 'Não é possível reabastecer @val. Desligue a base e utilize uma esponja '
                         'de melamina húmida para limpar os contactos de carregamento no robô e na '
                         'base. (457)',
              'title': 'Problema de comunicação da base'}},
 464: {'de': {'content': 'Befüllen Sie den Reinigungsmitteltank mit dem StayClean™ '
                         'Wischkonzentrat, damit es beim Wischen automatisch dosiert werden kann. '
                         'Oder schalten Sie die Funktion in den Robotereinstellungen aus. (464)',
              'title': 'Reinigungsmitteltank der Dockingstation leer'},
       'en': {'content': 'Fill detergent tank with StayClean™ Mopping Concentrate so it can '
                         'auto-dispense during mopping. Or turn off feature in Robot Settings. '
                         '(464)',
              'title': 'Dock Detergent tank empty'},
       'es': {'content': 'Llena el tanque de detergente con el concentrado para fregar StayClean™ '
                         'para que pueda dispensarse automáticamente durante el fregado. También '
                         'puedes desactivar la función en Configuración del robot. (464)',
              'title': 'Tanque de detergente de la base vacío'},
       'fr': {'content': 'Remplissez le réservoir de détergent avec le concentré de nettoyage à la '
                         'serpillière StayClean™ afin qu’il soit distribué automatiquement pendant '
                         'le nettoyage à la serpillière. Ou désactivez la fonctionnalité dans les '
                         'paramètres du robot. (464)',
              'title': 'Réservoir de détergent de la station d’accueil vide'},
       'it': {'content': 'Riempire il serbatoio del detergente con StayClean™ Mopping Concentrate '
                         "per consentirne l'erogazione automatica durante il lavaggio. Oppure "
                         'disattiva la funzione in Impostazioni robot. (464)',
              'title': 'Il serbatoio del detergente della base è vuoto'},
       'nl': {'content': 'Vul de reinigingsmiddeltank met StayClean™ Mopping Concentrate zodat '
                         'deze automatisch kan worden toegediend tijdens het dweilen. Of schakel '
                         'de functie uit in de robotinstellingen. (464)',
              'title': 'Reinigingsmiddeltank dock leeg'},
       'pl': {'content': 'Napełnij zbiornik na detergent koncentratem do mycia mopem StayClean™, '
                         'aby mógł być automatycznie dozowany podczas mycia mopem. Można też '
                         'wyłączyć tę funkcję w Ustawieniach robota. (464)',
              'title': 'Zbiornik na detergent w stacji dokującej jest pusty'},
       'pt': {'content': 'Encha o depósito de detergente com StayClean™ Mopping Concentrate para '
                         'distribuição automática durante a lavagem. Ou desative a funcionalidade '
                         'nas Definições do Robô. (464)',
              'title': 'Depósito de detergente da base vazio'}},
 510: {'de': {'content': 'Bitte warten Sie vor der Reinigung, bis das Update abgeschlossen ist. '
                         'Dies sollte weniger als 20 Minuten dauern. (510)',
              'title': 'Dockingstation-Update läuft'},
       'en': {'content': 'Please wait for update to complete before cleaning. This should take '
                         'under 20 minutes. (510)',
              'title': 'Dock update in progress'},
       'es': {'content': 'Espera a que se complete la actualización antes de limpiar. Debería '
                         'tardar menos de 20\xa0minutos. (510)',
              'title': 'Actualización de la base en curso'},
       'fr': {'content': 'Veuillez patienter jusqu’à la fin de la mise à jour avant de procéder au '
                         'nettoyage. Cela devrait prendre moins de 20 minutes. (510)',
              'title': 'Mise à jour de la station d’accueil en cours'},
       'it': {'content': "Attendere il completamento dell'aggiornamento prima di eseguire la "
                         'pulizia. Dovrebbe richiedere meno di 20 minuti. (510)',
              'title': 'Aggiornamento della base in corso'},
       'nl': {'content': 'Wacht tot de update is voltooid voordat u gaat schoonmaken. Dit zou '
                         'minder dan 20 minuten moeten duren. (510)',
              'title': 'Dock-update in uitvoering'},
       'pl': {'content': 'Przed rozpoczęciem sprzątania poczekaj na zakończenie aktualizacji. '
                         'Powinno to potrwać mniej niż 20\xa0minut. (510)',
              'title': 'Trwa aktualizacja stacji dokującej'},
       'pt': {'content': 'Aguarde que a atualização termine antes de iniciar a limpeza. Isto deve '
                         'demorar menos de 20 minutos. (510)',
              'title': 'Atualização da base em curso'}},
 513: {'de': {'content': 'Schließen Sie die Dockingstation erneut an',
              'title': 'Wischen und Moppwäsche nicht verfügbar: Pumpenproblem'},
       'en': {'content': 'Replug Dock to restart and enable Mopping and Mop Wash. (513)',
              'title': 'Mopping and mop wash unavailable: pump issue'},
       'es': {'content': 'Vuelve a enchufar la base para reiniciarla y activar el fregado y el '
                         'lavado de la mopa. (513)',
              'title': 'Fregado y lavado de mopa no disponibles: problema con la bomba'},
       'fr': {'content': 'Rebranchez la station d’accueil pour la redémarrer et activer le '
                         'nettoyage à la serpillière et le lavage de la serpillière. (513)',
              'title': 'Nettoyage à la serpillière et lavage de serpillière indisponibles : '
                       'problème de pompe'},
       'it': {'content': 'Ricollega la base per riavviarla e abilitare il lavaggio e il lavaggio '
                         'del mop. (513)',
              'title': 'Lavaggio pavimento e lavaggio panno non disponibili: problema alla pompa'},
       'nl': {'content': 'Sluit het basisstation opnieuw aan om opnieuw te starten en dweilen en '
                         'mop wassen in te schakelen. (513)',
              'title': 'Dweilen en dweilwassen niet beschikbaar: pompprobleem'},
       'pl': {'content': 'Podłącz ponownie stację dokującą, aby ją zrestartować i włączyć '
                         'mopowanie oraz mycie mopa. (513)',
              'title': 'Mycie mopem i mycie mopa niedostępne: problem z pompą'},
       'pt': {'content': 'Volte a ligar a base para reiniciar e ativar a lavagem do chão e a '
                         'lavagem da esfregona. (513)',
              'title': 'Lavagem e limpeza da mopa indisponíveis: problema na bomba'}},
 517: {'de': {'content': 'Reinigen Sie den Schmutzwasserbehälter von @val mit milder Seife und '
                         'prüfen Sie ihn auf Verstopfungen. Wischen Sie das Mopp-Reinigungsbecken '
                         'und die Kanalbelüftung der Dockingstation mit einem sauberen, trockenen '
                         'Tuch ab (517)',
              'title': 'Problem mit Moppwäsche: Schmutzwasserbehälter und Dockingstation reinigen'},
       'en': {'content': "Clean\xa0@val's Dirty Water Tank with mild soap and check for clogs. "
                         "Wipe the Dock's Mop Wash Tray and Air Duct Vent with a clean, dry cloth "
                         '(517)',
              'title': 'Mop wash issue: Clean dirty water tank and dock'},
       'es': {'content': 'Limpia el depósito de agua sucia de @val con un jabón suave y comprueba '
                         'que no haya obstrucciones. Limpia la cubeta de lavado de la mopa y el '
                         'conducto de ventilación de la base con un paño limpio y seco (517)',
              'title': 'Problema de lavado de mopa: Limpia el depósito de agua sucia y la base'},
       'fr': {'content': 'Nettoyez le bac d’eau sale de @val avec un savon doux et vérifiez s’il y '
                         'a des obstructions. Essuyez le bac de lavage de serpillière de la '
                         'station d’accueil et l’ouverture du conduit d’aspiration avec un chiffon '
                         'propre et sec (517)',
              'title': 'Problème de lavage de serpillière : Nettoyez le bac d’eau sale et la '
                       'station d’accueil'},
       'it': {'content': "Pulire il serbatoio dell'acqua sporca di @val con un sapone neutro e "
                         'controllare se ci sono ostruzioni. Pulire la vaschetta per il lavaggio '
                         "del panno della base e la presa d'aria con un panno pulito e asciutto "
                         '(517)',
              'title': "Problema con il lavaggio del panno: Pulire il serbatoio dell'acqua sporca "
                       'e la base'},
       'nl': {'content': 'Maak de opvangbak voor vuil water van @val schoon met milde zeep en '
                         'controleer op verstoppingen. Veeg de dweilwaskom en het luchtkanaal van '
                         'het basisstation schoon met een schone, droge doek (517)',
              'title': 'Probleem met dweilwassen: Reinig de opvangbak voor vuil water en het '
                       'basisstation'},
       'pl': {'content': 'Wyczyść zbiornik na brudną wodę robota @val łagodnym mydłem i sprawdź, '
                         'czy brak zatorów. Przetrzyj czystą, suchą szmatką nieckę mycia mopa w '
                         'stacji dokującej oraz otwór wentylacyjny (517)',
              'title': 'Problem z myciem mopa: Wyczyść zbiornik na brudną wodę i stację dokującą'},
       'pt': {'content': 'Lave o depósito de água suja de @val com sabão neutro e verifique se '
                         'existem obstruções. Limpe o recipiente de lavagem da mopa da base e a '
                         'ventilação com um pano limpo e seco (517)',
              'title': 'Problema de lavagem da mopa: Limpe o depósito de água suja e a base'}},
 520: {'de': {'content': 'Reinigen Sie die IR-Fenster an @val und der Dockingstation mit einem '
                         'sauberen, trockenen Tuch. (520)',
              'title': '@val kann nicht mit der Dockingstation kommunizieren'},
       'en': {'content': 'Clean the IR windows on\xa0@val\xa0and the dock with a clean, dry cloth. '
                         '(520)',
              'title': "@val\xa0can't communicate with its Dock"},
       'es': {'content': 'Limpia las ventanas de infrarrojos de @val y la base con un paño limpio '
                         'y seco. (520)',
              'title': '@val no puede comunicarse con la base'},
       'fr': {'content': 'Nettoyez les fenêtres IR de @val et de la station d’accueil avec un '
                         'chiffon propre et sec. (520)',
              'title': '@val ne peut pas communiquer avec sa station d’accueil'},
       'it': {'content': 'Pulire le finestre a infrarossi su @val e sulla base con un panno pulito '
                         'e asciutto. (520)',
              'title': '@val non riesce a comunicare con la sua base'},
       'nl': {'content': 'Maak de IR-vensters op @val en het dock schoon met een schone, droge '
                         'doek. (520)',
              'title': '@val kan niet communiceren met het basisstation'},
       'pl': {'content': 'Wyczyść okienka podczerwieni na robocie @val i stacji dokującej czystą, '
                         'suchą szmatką. (520)',
              'title': 'Robot @val nie może nawiązać połączenia ze stacją dokującą'},
       'pt': {'content': 'Limpe as janelas IR em @val e na base com um pano limpo e seco. (520)',
              'title': '@val não consegue comunicar com a base'}},
 653: {'de': {'content': 'Setzen Sie den Schmutzwassertank wieder ein, um Wischen und Moppwäsche '
                         'zu ermöglichen. (653)',
              'title': 'Schmutzwassertank fehlt'},
       'en': {'content': 'Reinstall dirty water tank to enable mopping and mop wash. (653)',
              'title': 'Dirty water tank missing'},
       'es': {'content': 'Vuelve a instalar el tanque de agua sucia para permitir el fregado y el '
                         'lavado de la mopa. (653)',
              'title': 'Falta el tanque de agua sucia'},
       'fr': {'content': 'Réinstallez le réservoir d’eau sale pour activer le nettoyage à la '
                         'serpillière et le lavage de serpillière. (653)',
              'title': 'Réservoir d’eau sale manquant'},
       'it': {'content': "Reinstallare il serbatoio dell'acqua sporca per abilitare il lavaggio e "
                         'la pulizia del panno. (653)',
              'title': "Serbatoio dell'acqua sporca mancante"},
       'nl': {'content': 'Installeer de vuilwatertank opnieuw om te kunnen dweilen en de dweil te '
                         'wassen. (653)',
              'title': 'Vuilwatertank ontbreekt'},
       'pl': {'content': 'Zamontuj ponownie zbiornik na brudną wodę, aby umożliwić mycie mopem i '
                         'mycie mopa. (653)',
              'title': 'Brak zbiornika na brudną wodę'},
       'pt': {'content': 'Volte a instalar o depósito de água suja para ativar a lavagem e a '
                         'limpeza da mopa. (653)',
              'title': 'Depósito de água suja em falta'}},
 654: {'de': {'content': 'Leeren Sie den Schmutzwassertank und setzen Sie ihn wieder ein, um das '
                         'Wischen und die Moppwäsche zu ermöglichen. (654)',
              'title': 'Schmutzwassertank voll'},
       'en': {'content': 'Empty dirty water tank and reinstall to enable mopping and mop wash. '
                         '(654)',
              'title': 'Dirty water tank full'},
       'es': {'content': 'Vacía el tanque de agua sucia y vuelve a instalarlo para permitir el '
                         'fregado y el lavado de la mopa. (654)',
              'title': 'Tanque de agua sucia lleno'},
       'fr': {'content': 'Videz le réservoir d’eau sale et réinstallez-le pour activer le '
                         'nettoyage à la serpillière et le lavage de serpillière. (654)',
              'title': 'Réservoir d’eau sale plein'},
       'it': {'content': "Svuotare il serbatoio dell'acqua sporca e reinstallarlo per abilitare il "
                         'lavaggio e la pulizia del panno. (654)',
              'title': "Serbatoio dell'acqua sporca pieno"},
       'nl': {'content': 'Leeg de vuilwatertank en plaats deze terug om het dweilen en het wassen '
                         'van de dweil in te schakelen. (654)',
              'title': 'Vuilwatertank vol'},
       'pl': {'content': 'Opróżnij zbiornik na brudną wodę i zamontuj go ponownie, aby umożliwić '
                         'mycie mopem i mycie mopa. (654)',
              'title': 'Zapełniony zbiornik na brudną wodę'},
       'pt': {'content': 'Esvazie o depósito de água suja e volte a instalar para ativar a lavagem '
                         'e a limpeza da mopa. (654)',
              'title': 'Depósito de água suja cheio'}},
 660: {'de': {'content': 'Stecken Sie die Dockingstation vom Stromnetz aus und reinigen Sie die '
                         'Ladekontakte an Roboter und Dockingstation mit einem feuchten '
                         'Schmutzradierer. (660)',
              'title': 'Kommunikationsproblem mit der Dockingstation während der Moppwäsche'},
       'en': {'content': 'Unplug the Dock, then wipe the Charging Contacts on Robot and Dock with '
                         'a slightly damp tissue. (660)',
              'title': 'Dock communication issue during mop wash'},
       'es': {'content': 'Desenchufa la base y limpia los contactos de carga del robot y de la '
                         'base con un pañuelo ligeramente húmedo. (660)',
              'title': 'Problema de comunicación con la base durante el lavado de la mopa'},
       'fr': {'content': 'Débranchez la station d’accueil, puis essuyez les contacts de chargement '
                         'du robot et de la station d’accueil avec un mouchoir légèrement humide. '
                         '(660)',
              'title': 'Problème de communication avec la station d’accueil pendant le lavage de '
                       'serpillière'},
       'it': {'content': 'Scollegare la base, quindi pulire i contatti di ricarica sul robot e '
                         'sulla base con un fazzoletto leggermente umido. (660)',
              'title': 'Problema di comunicazione della base durante la pulizia del panno'},
       'nl': {'content': 'Haal de stekker van het basisstation uit het stopcontact en veeg de '
                         'oplaadcontacten op de robot en het basisstation schoon met een licht '
                         'vochtig doekje. (660)',
              'title': 'Communicatieprobleem met het dock tijdens het wassen van de dweil'},
       'pl': {'content': 'Odłącz stację dokującą, a następnie przetrzyj styki ładowania robota i '
                         'stacji dokującej lekko wilgotną ściereczką. (660)',
              'title': 'Problem z komunikacją ze stacją dokującą podczas mycia mopa'},
       'pt': {'content': 'Desligue a base e limpe os contactos de carregamento no robô e na base '
                         'com um lenço ligeiramente húmido. (660)',
              'title': 'Problema de comunicação com a base durante a lavagem da mopa'}},
 668: {'de': {'content': 'Bitte installieren Sie den Mopp oder setzen Sie ihn neu ein, um Wischen '
                         'und Moppwäsche zu ermöglichen. (668)',
              'title': 'Kein Mopp angebracht'},
       'en': {'content': 'Please install or reseat mop to enable mopping and mop wash. (668)',
              'title': 'No mop attached'},
       'es': {'content': 'Instala o vuelve a colocar la mopa para permitir el fregado y el lavado '
                         'de la mopa. (668)',
              'title': 'Mopa no instalada'},
       'fr': {'content': 'Veuillez installer ou repositionner la serpillière pour activer le '
                         'nettoyage à la serpillière et le lavage de serpillière. (668)',
              'title': 'Aucune serpillière fixée'},
       'it': {'content': 'Installare o reinserire il panno per abilitare il lavaggio del pavimento '
                         'e la pulizia del panno. (668)',
              'title': 'Nessun panno di lavaggio inserito'},
       'nl': {'content': 'Installeer of plaats de dweil opnieuw om dweilen en dweilwassen in te '
                         'schakelen. (668)',
              'title': 'Geen dweil bevestigd'},
       'pl': {'content': 'Zamontuj lub popraw mopa, aby włączyć mycie mopem i mycie mopa. (668)',
              'title': 'Nie podłączono mopa'},
       'pt': {'content': 'Instale ou reposicione a mopa para ativar a lavagem e a limpeza da mopa. '
                         '(668)',
              'title': 'Sem mopa instalada'}},
 669: {'de': {'content': 'Prüfen Sie den Mopp auf Blockierungen und starten Sie den Roboter neu. '
                         'Nehmen Sie den Roboter aus der Dockingstation und drücken Sie den '
                         'Netzschalter 10 Sekunden lang, um ihn auszuschalten. Drücken Sie ihn '
                         'dann erneut 3 Sekunden lang, um ihn einzuschalten. (669)',
              'title': 'Mopp hat sich während der Moppwäsche verklemmt'},
       'en': {'content': 'Check Mop for obstructions and restart the Robot. Move the Robot out of '
                         'the Dock and press the power button for 10 seconds to turn it off, then '
                         'press again for 3 seconds to turn it on. (669)',
              'title': 'Mop got stuck during mop wash'},
       'es': {'content': 'Comprueba si la mopa está obstruida y reinicia el robot. Saca el robot '
                         'de la base y mantén pulsado el botón de encendido durante 10 segundos '
                         'para apagarlo. Luego vuelve a pulsarlo durante 3 segundos para '
                         'encenderlo. (669)',
              'title': 'La mopa se ha atascado durante el lavado de la mopa'},
       'fr': {'content': 'Vérifiez que la serpillière n’est pas bloquée et redémarrez le robot. '
                         'Sortez le robot de la station d’accueil, appuyez sur le bouton '
                         'd’alimentation pendant 10 secondes pour l’éteindre, puis appuyez à '
                         'nouveau pendant 3 secondes pour le rallumer. (669)',
              'title': 'La serpillière s’est bloquée pendant le lavage de serpillière'},
       'it': {'content': 'Controlla che il panno non sia ostruito e riavvia il robot. Sposta il '
                         'robot fuori dalla base e premi il pulsante di accensione per 10 secondi '
                         'per spegnerlo, quindi premilo di nuovo per 3 secondi per accenderlo. '
                         '(669)',
              'title': 'Il panno si è bloccato durante la pulizia del panno'},
       'nl': {'content': 'Controleer de Mop op verstoppingen en start de Robot opnieuw. Haal de '
                         'Robot uit de Dock en druk 10 seconden op de aan/uit-knop om hem uit te '
                         'schakelen. Druk daarna opnieuw 3 seconden om hem in te schakelen. (669)',
              'title': 'Dweil is vastgelopen tijdens het wassen van de dweil'},
       'pl': {'content': 'Sprawdź, czy mop nie jest zablokowany, i uruchom robota ponownie. Wyjmij '
                         'robota ze stacji dokującej i naciśnij przycisk zasilania na 10 sekund, '
                         'aby go wyłączyć, a następnie ponownie naciśnij przez 3 sekund, aby go '
                         'włączyć. (669)',
              'title': 'Mop zablokował się podczas mycia mopa'},
       'pt': {'content': 'Verifique se a mopa está obstruída e reinicie o Robot. Retire o Robot da '
                         'Dock e prima o botão de alimentação durante 10 segundos para o desligar. '
                         'Depois, prima novamente durante 3 segundos para o ligar. (669)',
              'title': 'A mopa ficou presa durante a lavagem'}},
 670: {'de': {'content': 'Stellen Sie sicher, dass das Mopp-Reinigungsbecken der Dockingstation '
                         'und der Filter ordnungsgemäß installiert sind, um Wischen und '
                         'Wischtuch-Wäsche zu ermöglichen. (670)',
              'title': 'Mopp-Reinigungsbecken benötigt Aufmerksamkeit'},
       'en': {'content': "Make sure the Dock's Mop Cleaning Tray and Filter are properly installed "
                         'to enable Mopping and Mop Wash. (670)',
              'title': 'Mop wash basin needs attention'},
       'es': {'content': 'Asegúrate de que la cubeta de lavado de la mopa y el filtro de la base '
                         'estén instalados correctamente para permitir el fregado y el lavado de '
                         'la mopa. (670)',
              'title': 'La cubeta de lavado de la mopa requiere atención'},
       'fr': {'content': 'Assurez-vous que le bac de lavage de serpillière de la station d’accueil '
                         'et le filtre sont correctement installés pour activer le nettoyage à la '
                         'serpillière et le lavage de lingette. (670)',
              'title': 'Le bac de lavage de serpillière nécessite une intervention'},
       'it': {'content': 'Assicurarsi che la vaschetta di lavaggio del panno della base e il '
                         'filtro siano installati correttamente per abilitare il lavaggio del '
                         'pavimento e il lavaggio del panno. (670)',
              'title': 'La vaschetta di lavaggio del panno richiede attenzione'},
       'nl': {'content': 'Zorg ervoor dat de wasbak en het filter van het basisstation goed zijn '
                         'geïnstalleerd om dweilen en het wassen van de dweil mogelijk te maken. '
                         '(670)',
              'title': 'De dweilwasbak heeft aandacht nodig'},
       'pl': {'content': 'Upewnij się, że niecka mycia mopa oraz filtr są prawidłowo zamontowane, '
                         'aby umożliwić mycie mopem i mycie nakładki. (670)',
              'title': 'Niecka mycia mopa wymaga uwagi'},
       'pt': {'content': 'Certifique-se de que o recipiente de lavagem da mopa da base e o filtro '
                         'estão corretamente instalados para ativar a lavagem e limpeza da mopa. '
                         '(670)',
              'title': 'O recipiente de lavagem da mopa precisa de atenção'}},
 671: {'de': {'content': 'Bitte füllen Sie den Dockingstation-Tank auf und installieren Sie ihn, '
                         'um Wischen und Moppwäsche zu ermöglichen. (671)',
              'title': 'Dockingstation-Tank ist leer oder nicht installiert'},
       'en': {'content': 'Fill up and install the clean water tank to enable mopping and mop wash. '
                         '(671)',
              'title': 'Clean Water Tank empty or not installed'},
       'es': {'content': 'Rellena e instala el tanque de la base para permitir el fregado y el '
                         'lavado de la mopa. (671)',
              'title': 'El tanque de la base está vacío o no está instalado'},
       'fr': {'content': 'Veuillez remplir et installer le réservoir de la station d’accueil pour '
                         'activer le nettoyage à la serpillière et le lavage de serpillière. (671)',
              'title': 'Le réservoir de la station d’accueil est vide ou n’est pas installé'},
       'it': {'content': 'Riempire e installare il serbatoio della base per abilitare il lavaggio '
                         'del pavimento e la pulizia del panno. (671)',
              'title': 'Serbatoio della base vuoto o non installato'},
       'nl': {'content': 'Vul de tank van het basisstation bij en installeer deze om te kunnen '
                         'dweilen en de dweil te wassen. (671)',
              'title': 'Tank van het basisstation is leeg of niet geïnstalleerd'},
       'pl': {'content': 'Napełnij zbiornik i zamontuj go w stacji dokującej, aby umożliwić mycie '
                         'mopem i mycie mopa. (671)',
              'title': 'Zbiornik na czystą wodę jest pusty lub nie został zamontowany'},
       'pt': {'content': 'Encha e instale o depósito da base para ativar a lavagem e a limpeza da '
                         'mopa. (671)',
              'title': 'O depósito da base está vazio ou não instalado'}},
 672: {'de': {'content': 'Bitte leeren und installieren Sie den Schmutzwassertank der '
                         'Dockingstation, um das Wischen und die Moppwäsche zu ermöglichen. (672)',
              'title': 'Schmutzwassertank ist voll oder nicht installiert'},
       'en': {'content': "Empty and install the Dock's Dirty Water Tank to enable Mopping and Mop "
                         'Wash. (672)',
              'title': 'Dirty water tank is full or not installed'},
       'es': {'content': 'Vacía e instala el tanque de agua sucia de la base para permitir el '
                         'fregado y el lavado de la mopa. (672)',
              'title': 'Tanque de agua sucia lleno o no instalado'},
       'fr': {'content': 'Veuillez vider et installer le réservoir d’eau sale de la station '
                         'd’accueil pour activer le nettoyage à la serpillière et le lavage de '
                         'serpillière. (672)',
              'title': 'Le réservoir d’eau sale est plein ou non installé'},
       'it': {'content': 'Per consentire il lavaggio del pavimento e la pulizia del panno, '
                         "svuotare e installare il serbatoio dell'acqua sporca della base. (672)",
              'title': "Il serbatoio dell'acqua sporca è pieno o non installato"},
       'nl': {'content': 'Leeg de vuilwatertank van het basisstation en plaats deze terug om te '
                         'kunnen dweilen en de dweil te wassen. (672)',
              'title': 'Vuilwatertank is vol of niet geïnstalleerd'},
       'pl': {'content': 'Opróżnij i zamontuj zbiornik na brudną wodę w stacji dokującej, aby '
                         'umożliwić mycie mopem i mycie mopa. (672)',
              'title': 'Zbiornik na brudną wodę jest pełny lub niezamontowany'},
       'pt': {'content': 'Esvazie e instale o depósito de água suja da base para ativar a lavagem '
                         'e a limpeza da mopa. (672)',
              'title': 'O depósito de água suja está cheio ou não instalado'}},
 751: {'de': {'content': 'Stecken Sie die Dockingstation aus, warten Sie 30 Sekunden und stecken '
                         'Sie sie wieder ein. (751)',
              'title': 'Mopptrocknung nicht verfügbar: Gebläseproblem'},
       'en': {'content': 'Unplug the Dock, wait 30s and plug back in. (751)',
              'title': 'Mop dry unavailable: blower issue'},
       'es': {'content': 'Desenchufa la base, espera 30\xa0segundos y vuelve a enchufarla. (751)',
              'title': 'Secado de la mopa no disponible: problema del ventilador'},
       'fr': {'content': 'Débranchez la station d’accueil, attendez 30 secondes et rebranchez-la. '
                         '(751)',
              'title': 'Séchage de la serpillière indisponible : problème de soufflerie'},
       'it': {'content': 'Scollegare la stazione di ricarica, attendere 30 secondi e ricollegarla. '
                         '(751)',
              'title': 'Asciugatura panno non disponibile: problema alla ventola'},
       'nl': {'content': 'Haal de stekker van het basisstation uit het stopcontact, wacht 30 '
                         'seconden en steek deze er weer in. (751)',
              'title': 'Dweildrogen niet beschikbaar: probleem met ventilator'},
       'pl': {'content': 'Odłącz stację dokującą od zasilania, odczekaj 30\xa0sekund i podłącz '
                         'ponownie. (751)',
              'title': 'Suszenie mopa niedostępne: problem z dmuchawą'},
       'pt': {'content': 'Desligue a base, aguarde 30 segundos e volte a ligar. (751)',
              'title': 'Secagem da mopa indisponível: problema no ventilador'}},
 752: {'de': {'content': 'Prüfen Sie den Wischmopp auf Hindernisse und starten Sie den Roboter '
                         'neu: Nehmen Sie den Roboter von der Dockingstation, halten Sie die '
                         'Ein-/Aus-Taste 10 s und dann 3s gedrückt. (752)',
              'title': 'Mopptrocknung nicht verfügbar: Mopp konnte zum Trocknen nicht angehoben '
                       'werden'},
       'en': {'content': 'Check Mop for obstructions and restart the Robot: Move the Robot out of '
                         'the Dock, hold the Power button for 10s then 3s. (752)',
              'title': 'Mop dry unavailable: mop could not lift to dry'},
       'es': {'content': 'Comprueba si la mopa tiene obstrucciones y reinicia el robot: saca el '
                         'robot de la base, mantén pulsado el botón de encendido 10 s y luego 3s. '
                         '(752)',
              'title': 'Secado de la mopa no disponible: la mopa no se ha podido levantar para '
                       'secarse'},
       'fr': {'content': 'Vérifiez que la serpillière n’est pas obstruée et redémarrez le robot : '
                         'sortez le robot de la station d’accueil, maintenez le bouton '
                         'd’alimentation enfoncé 10 s puis 3s. (752)',
              'title': 'Séchage de la serpillière indisponible : la serpillière n’a pas pu se '
                       'soulever pour le séchage'},
       'it': {'content': 'Controlla che il mop non sia ostruito e riavvia il robot: sposta il '
                         'robot fuori dalla base, tieni premuto il pulsante di accensione per 10 s '
                         'e poi per 3s. (752)',
              'title': 'Asciugatura panno non disponibile: impossibile sollevare il panno per '
                       "l'asciugatura"},
       'nl': {'content': 'Controleer de mop op obstakels en start de robot opnieuw: haal de robot '
                         'van het basisstation, houd de aan/uit-knop 10 s en daarna 3s ingedrukt. '
                         '(752)',
              'title': 'Dweildrogen niet beschikbaar: dweil kon niet omhoog komen om te drogen'},
       'pl': {'content': 'Sprawdź, czy mop nie jest zablokowany, i uruchom ponownie robota: '
                         'Zdejmij robota ze stacji dokującej, przytrzymaj przycisk zasilania przez '
                         '10 s, a następnie przez 3s. (752)',
              'title': 'Suszenie mopa niedostępne: nie udało się unieść mopa do osuszenia'},
       'pt': {'content': 'Verifique se existem obstruções na esfregona e reinicie o robô: retire o '
                         'robô da base, mantenha o botão de alimentação premido por 10 s e depois '
                         'por 3s. (752)',
              'title': 'Secagem da mopa indisponível: a mopa não conseguiu levantar para secar'}},
 756: {'de': {'content': 'Bitte installieren Sie den Mopp oder setzen Sie ihn neu ein. (756)',
              'title': 'Mopptrocknung nicht verfügbar: Kein Mopp angebracht'},
       'en': {'content': 'Please install or reseat mop. (756)',
              'title': 'Mop dry unavailable: no mop attached'},
       'es': {'content': 'Instala o vuelve a colocar la mopa. (756)',
              'title': 'Secado de la mopa no disponible: mopa no instalada'},
       'fr': {'content': 'Veuillez installer ou repositionner la serpillière. (756)',
              'title': 'Séchage de la serpillière indisponible : aucune serpillière fixée'},
       'it': {'content': 'Installare o riposizionare il panno. (756)',
              'title': 'Asciugatura panno non disponibile: nessun panno installato'},
       'nl': {'content': 'Installeer de dweil of plaats deze opnieuw. (756)',
              'title': 'Dweildrogen niet beschikbaar: geen dweil bevestigd'},
       'pl': {'content': 'Zamontuj lub popraw mopa. (756)',
              'title': 'Suszenie mopa niedostępne: nie przymocowano mopa'},
       'pt': {'content': 'Instale ou reposicione a mopa. (756)',
              'title': 'Secagem da mopa indisponível: sem mopa instalada'}},
 757: {'de': {'content': 'Stecken Sie die Dockingstation vom Stromnetz aus und reinigen Sie die '
                         'Ladekontakte an Roboter und Dockingstation mit einem feuchten '
                         'Schmutzradierer. (757)',
              'title': 'Mopptrocknung nicht verfügbar: Kommunikationsproblem mit der '
                       'Dockingstation'},
       'en': {'content': 'Unplug the Dock, then wipe the Charging Contacts on Robot and Dock with '
                         'a slightly damp tissue. (757)',
              'title': 'Mop dry unavailable: dock communication issue'},
       'es': {'content': 'Desenchufa la base y limpia los contactos de carga del robot y de la '
                         'base con un pañuelo ligeramente húmedo. (757)',
              'title': 'Secado de la mopa no disponible: problema de comunicación con la base'},
       'fr': {'content': 'Débranchez la station d’accueil, puis essuyez les contacts de chargement '
                         'du robot et de la station d’accueil avec un mouchoir légèrement humide. '
                         '(757)',
              'title': 'Séchage de la serpillière indisponible : problème de communication avec la '
                       'station d’accueil'},
       'it': {'content': 'Scollegare la base, quindi pulire i contatti di ricarica sul robot e '
                         'sulla base con un fazzoletto leggermente umido. (757)',
              'title': 'Asciugatura panno non disponibile: problema di comunicazione della base'},
       'nl': {'content': 'Haal de stekker van het basisstation uit het stopcontact en veeg de '
                         'oplaadcontacten op de robot en het basisstation schoon met een licht '
                         'vochtig doekje. (757)',
              'title': 'Dweildrogen niet beschikbaar: communicatieprobleem met dock'},
       'pl': {'content': 'Odłącz stację dokującą, a następnie przetrzyj styki ładowania robota i '
                         'stacji dokującej lekko wilgotną ściereczką. (757)',
              'title': 'Suszenie mopa niedostępne: problem z komunikacją ze stacją dokującą'},
       'pt': {'content': 'Desligue a base e limpe os contactos de carregamento no robô e na base '
                         'com um lenço ligeiramente húmido. (757)',
              'title': 'Secagem da mopa indisponível: problema de comunicação da base'}},
 1000: {'de': {'content': 'Ziehen Sie verhedderte Fasern und Schmutz heraus, damit sich die '
                          'Seitenbürste frei drehen kann. (1000)',
               'title': 'Linke Seitenbürste klemmt'},
        'en': {'content': 'Pull tangled fibers and debris from the side brush can spin freely. '
                          '(1000)',
               'title': 'Left side brush is stuck'},
        'es': {'content': 'Retira las fibras enredadas y los residuos del cepillo de bordes para '
                          'que pueda girar libremente. (1000)',
               'title': 'El cepillo de bordes izquierdo está atascado'},
        'fr': {'content': 'Retirez les fibres et les débris emmêlés de la brosse latérale pour '
                          'qu’elle puisse tourner librement. (1\xa0000)',
               'title': 'La brosse latérale gauche est bloquée'},
        'it': {'content': 'Rimuovere le fibre e i detriti aggrovigliati in modo che la spazzola '
                          'laterale possa girare liberamente. (1000)',
               'title': 'La spazzola laterale sinistra è bloccata'},
        'nl': {'content': 'Verwijder verwarde vezels en vuil van de zijborstel, zodat deze weer '
                          'vrij kan draaien. (1000)',
               'title': 'Linkerzijborstel zit vast'},
        'pl': {'content': 'Usuń splątane włókna i zanieczyszczenia ze szczotki bocznej, aby mogła '
                          'swobodnie się obracać. (1000)',
               'title': 'Lewa szczotka boczna jest zablokowana'},
        'pt': {'content': 'Remova fibras e resíduos emaranhados para que a escova lateral possa '
                          'rodar livremente. (1000)',
               'title': 'A escova lateral esquerda está bloqueada'}},
 1001: {'de': {'content': 'Ziehen Sie verhedderte Fasern und Schmutz heraus, damit sich die '
                          'Seitenbürste frei drehen kann. (1001)',
               'title': 'Rechte Seitenbürste klemmt'},
        'en': {'content': 'Pull tangled fibers and debris from the side brush can spin freely. '
                          '(1001)',
               'title': 'Right side brush is stuck'},
        'es': {'content': 'Retira las fibras enredadas y los residuos del cepillo de bordes para '
                          'que pueda girar libremente. (1001)',
               'title': 'El cepillo de bordes derecho está atascado'},
        'fr': {'content': 'Retirez les fibres et les débris emmêlés de la brosse latérale pour '
                          'qu’elle puisse tourner librement. (1\xa0001)',
               'title': 'La brosse latérale droite est bloquée'},
        'it': {'content': 'Tirare le fibre e i detriti aggrovigliati in modo che la spazzola '
                          'laterale possa girare liberamente. (1001)',
               'title': 'La spazzola laterale destra è bloccata'},
        'nl': {'content': 'Verwijder verwarde vezels en vuil van de zijborstel, zodat deze weer '
                          'vrij kan draaien. (1001)',
               'title': 'Rechterzijborstel zit vast'},
        'pl': {'content': 'Usuń splątane włókna i zanieczyszczenia ze szczotki bocznej, aby mogła '
                          'swobodnie się obracać. (1001)',
               'title': 'Prawa szczotka boczna zablokowana'},
        'pt': {'content': 'Remova fibras e resíduos emaranhados para que a escova lateral possa '
                          'rodar livremente. (1001)',
               'title': 'A escova lateral direita está bloqueada'}},
 1008: {'de': {'content': 'Dieser Motor hebt oder senkt die Wischplatte von @val. Prüfen Sie die '
                          'Umgebung des Wischmopps auf Hindernisse und drücken Sie die '
                          'Ein-/Aus-Taste',
               'title': 'Motor für Moppanhebung blockiert'},
        'en': {'content': 'This motor is for\xa0@val\xa0to lift or lower its mop plate. Check for '
                          'obstructions around mop and press the Power button to resume Routine. '
                          '(1008)',
               'title': 'Mop lifting motor stalled'},
        'es': {'content': 'Este motor permite a @val subir o bajar la placa de la mopa. Comprueba '
                          'si hay obstrucciones alrededor de la mopa y pulsa el botón de encendido '
                          'para reanudar la rutina. (1008)',
               'title': 'Motor de elevación de la mopa atascado'},
        'fr': {'content': 'Ce moteur permet à @val de soulever ou d’abaisser son support de '
                          'serpillière. Vérifiez l’absence d’obstructions autour de la serpillière',
               'title': 'Moteur de levage de la serpillière bloqué'},
        'it': {'content': 'Questo motore consente a @val di sollevare o abbassare la piastra del '
                          'mop. Verifica che non vi siano ostruzioni intorno al mop e premi il '
                          'pulsante di accensione per riprendere la routine. (1008)',
               'title': 'Motore di sollevamento del panno in stallo'},
        'nl': {'content': 'Deze motor laat @val de mopplaat omhoog of omlaag bewegen. Controleer '
                          'op obstakels rond de mop en druk op de aan/uit-knop om de routine te '
                          'hervatten. (1008)',
               'title': 'Dweilhefmotor vastgelopen'},
        'pl': {'content': 'Ten silnik umożliwia robotowi @val podnoszenie lub opuszczanie płytki '
                          'mopującej. Sprawdź, czy wokół mopa nie ma przeszkód, i naciśnij '
                          'przycisk zasilania, aby wznowić rutynę. (1008)',
               'title': 'Silnik podnoszenia mopa zablokowany'},
        'pt': {'content': 'Este motor permite que @val levante ou baixe a placa da esfregona. '
                          'Verifique se existem obstruções à volta da esfregona e prima o botão de '
                          'alimentação para retomar a rotina. (1008)',
               'title': 'Motor de elevação da mopa bloqueado'}},
 1010: {'de': {'content': 'Stellen Sie sicher, dass der Pfad frei ist, damit @val zu seiner '
                          'Dockingstation zurückkehren kann. Überprüfen Sie, ob die Dockingstation '
                          'eingesteckt ist und sich an ihrem ursprünglichen Standort befindet. '
                          '(1010)',
               'title': '@val konnte nicht zur Dockingstation zurückkehren. Bewegen Sie ihn und '
                        'stellen Sie ihn zum Laden auf die Dockingstation.'},
        'en': {'content': 'Make sure the path is clear for\xa0@val\xa0to return to its Dock. Check '
                          'that the dock is plugged in and in its original location. (1010)',
               'title': "@val\xa0couldn't return to Dock. Move and place it on the Dock for "
                        'charging.'},
        'es': {'content': 'Asegúrate de que no haya obstáculos en el camino de vuelta a la base de '
                          '@val. Comprueba que la base esté enchufada y en su ubicación original. '
                          '(1010)',
               'title': '@val no ha podido volver a la base. Muévelo y colócalo en la base para '
                        'cargarlo.'},
        'fr': {'content': 'Assurez-vous que le chemin est dégagé pour que @val puisse retourner à '
                          'sa station d’accueil. Vérifiez que la station d’accueil est branchée et '
                          'qu’elle se trouve à son emplacement d’origine. (1\xa0010)',
               'title': '@val n’a pas pu retourner à la station d’accueil. Déplacez-le et '
                        'placez-le sur la station d’accueil pour le charger.'},
        'it': {'content': 'Assicurarsi che il percorso sia libero affinché @val possa tornare alla '
                          'sua base. Controllare che la base sia collegata e si trovi nella '
                          'posizione originale. (1010)',
               'title': '@val non è riuscito a tornare alla base. Spostalo e posizionalo sulla '
                        'base per la ricarica.'},
        'nl': {'content': 'Zorg ervoor dat het pad vrij is zodat @val kan terugkeren naar het '
                          'basisstation. Controleer of het dock is aangesloten en op de '
                          'oorspronkelijke locatie staat. (1010)',
               'title': '@val kon niet terugkeren naar het basisstation. Verplaats hem en plaats '
                        'hem op het basisstation om op te laden.'},
        'pl': {'content': 'Upewnij się, że droga jest wolna, aby robot @val mógł wrócić do stacji '
                          'dokującej. Sprawdź, czy stacja dokująca jest podłączona do zasilania i '
                          'znajduje się w swoim pierwotnym miejscu. (1010)',
               'title': 'Robot @val nie mógł wrócić do stacji dokującej. Przesuń go i umieść na '
                        'stacji dokującej w celu ładowania.'},
        'pt': {'content': 'Certifique-se de que o caminho está livre para @val regressar à base. '
                          'Verifique se a base está ligada e na sua localização original. (1010)',
               'title': '@val não conseguiu regressar à base. Mova-o e coloque-o na base para '
                        'carregar.'}},
 1025: {'de': {'content': 'Starten Sie @val neu, um den Fehler zu beheben. Entfernen Sie ihn von '
                          'der Dockingstation und halten Sie dann die Ein-/Aus-Taste 10 Sekunden '
                          'lang gedrückt. Halten Sie sie anschließend 3s lang gedrückt. (1025)',
               'title': 'Lasersensor-Problem'},
        'en': {'content': 'Restart\xa0@val\xa0to fix the issue. Move the Robot out of the Dock, '
                          'hold the Power button for 10s then 3s. (1025)',
               'title': 'Laser sensor issue'},
        'es': {'content': 'Reinicia @val para solucionar el error. Retíralo de la base y mantén '
                          'pulsado el botón de encendido durante 10\xa0segundos. Luego mantenlo '
                          'presionado 3s. (1025)',
               'title': 'Problema del sensor láser'},
        'fr': {'content': 'Redémarrez @val pour effacer l’erreur. Retirez-le de la station '
                          'd’accueil, puis maintenez le bouton d’alimentation enfoncé pendant 10 '
                          'secondes. (1\xa0025) Puis maintenez-le enfoncé pendant 3s.',
               'title': 'Problème de capteur laser'},
        'it': {'content': "Riavviare @val per risolvere l'errore. Rimuovere dalla base, quindi "
                          'tenere premuto il pulsante di accensione per 10 secondi. Quindi tienilo '
                          'premuto per 3 s. (1025)',
               'title': 'Problema al sensore laser'},
        'nl': {'content': 'Start @val opnieuw op om de fout te wissen. Verwijder het van het '
                          'basisstation en houd de aan/uit-knop 10 seconden ingedrukt. Houd deze '
                          'daarna 3 s ingedrukt. (1025)',
               'title': 'Probleem met lasersensor'},
        'pl': {'content': 'Uruchom ponownie robota @val w celu usunięcia błędu. Wyjmij ze stacji '
                          'dokującej, a następnie naciśnij i przytrzymaj przycisk zasilania przez '
                          '10\xa0sekund. Następnie przytrzymaj przez 3 s. (1025)',
               'title': 'Problem z czujnikiem laserowym'},
        'pt': {'content': 'Reinicie @val para corrigir o erro. Retire da base e depois prima sem '
                          'soltar o botão de alimentação durante 10 segundos. Em seguida, mantenha '
                          'premido por 3 s. (1025)',
               'title': 'Problema no sensor laser'}},
 1026: {'de': {'content': 'Überprüfen Sie den Mopp auf Verhedderungen oder Hindernisse und drücken '
                          'Sie die Ein-/Aus-Taste, um die Routine fortzusetzen. (1026)',
               'title': 'Mopp ist verheddert oder klemmt'},
        'en': {'content': 'Check mop for tangles or obstructions and press Power button to resume '
                          'routine. (1026)',
               'title': 'Mop is tangled or stuck'},
        'es': {'content': 'Comprueba si la mopa está atascada u obstruida y pulsa el botón de '
                          'encendido para reanudar la rutina. (1026)',
               'title': 'La mopa está atascada o enredada'},
        'fr': {'content': "Vérifiez que la serpillière n'est pas emmêlée ou bloquée et appuyez sur "
                          'le bouton d’alimentation pour reprendre la routine. (1\xa0026)',
               'title': 'La serpillière est emmêlée ou bloquée'},
        'it': {'content': 'Controllare se il panno presenta grovigli o ostruzioni e premere il '
                          'pulsante di accensione per riprendere la routine. (1026)',
               'title': 'Il panno è aggrovigliato o bloccato'},
        'nl': {'content': 'Controleer de dweil op klitten of obstakels en druk op de aan-/uitknop '
                          'om de routine te hervatten. (1026)',
               'title': 'Dweil is verstrikt of zit vast'},
        'pl': {'content': 'Sprawdź, czy mop nie jest splątany ani zablokowany, po czym naciśnij '
                          'przycisk zasilania, aby wznowić rutynę. (1026)',
               'title': 'Mop jest splątany lub zablokowany'},
        'pt': {'content': 'Verifique se existem enredos ou obstruções na mopa e prima o botão de '
                          'alimentação para retomar a rotina. (1026)',
               'title': 'A mopa está enredada ou bloqueada'}},
 1027: {'de': {'content': 'Überprüfen Sie, ob der saubere Wassertank richtig installiert ist und '
                          'ob er nachgefüllt werden muss.',
               'title': 'Der saubere Wassertank ist nicht eingesetzt oder der Wasserstand ist zu '
                        'niedrig.'},
        'en': {'content': 'Check whether the clean water tank is properly installed and see if it '
                          'needs refilling.',
               'title': 'Clean water tank is not in place or water level is too low'},
        'es': {'content': 'Compruebe que el depósito de agua limpia está correctamente instalado y '
                          'vea si necesita rellenarse.',
               'title': 'El depósito de agua limpia no está colocado o el nivel de agua es '
                        'demasiado bajo.'},
        'fr': {'content': "Vérifiez que le réservoir d'eau propre est correctement installé et "
                          "voyez s'il doit être rempli.",
               'title': "Le réservoir d'eau propre n'est pas en place ou le niveau d'eau est trop "
                        'bas.'},
        'it': {'content': "Verificare che il serbatoio dell'acqua pulita sia installato "
                          'correttamente e vedere se necessita di riempimento.',
               'title': "Il serbatoio dell'acqua pulita non è in posizione o il livello dell'acqua "
                        'è troppo basso.'},
        'nl': {'content': 'Controleer of de schone watertank correct is geïnstalleerd en of deze '
                          'moet worden bijgevuld.',
               'title': 'De schone watertank is niet op zijn plaats of het waterniveau is te '
                        'laag.'},
        'pl': {'content': 'Sprawdź, czy zbiornik na czystą wodę jest prawidłowo zainstalowany i '
                          'czy wymaga uzupełnienia.',
               'title': 'Zbiornik na czystą wodę nie jest na miejscu lub poziom wody jest zbyt '
                        'niski.'},
        'pt': {'content': 'Verifique se o depósito de água limpa está instalado corretamente e '
                          'veja se precisa de ser reabastecido.',
               'title': 'O depósito de água limpa não está no lugar ou o nível de água está '
                        'demasiado baixo.'}},
 1028: {'de': {'content': 'Prüfen Sie den Bereich um die Dockingstation auf Lecks und leeren Sie '
                          'den Schmutzwasserbehälter des Roboters. Befolgen Sie danach die '
                          'Schritte zur Fehlerbehebung, um mögliche Verstopfungen zu beseitigen. '
                          '(1028)',
               'title': 'Schmutzwasserbehälter oder Wischtuch-Waschbecken möglicherweise '
                        'verstopft'},
        'en': {'content': 'Check for leaks around the dock and empty the robot dirty water '
                          'Container. Next, follow steps to troubleshoot and clear any possible '
                          'clogs. (1028)',
               'title': 'Dirty water tank or washing basin may be clogged.'},
        'es': {'content': 'Comprueba si hay fugas alrededor de la base y vacía el depósito de agua '
                          'sucia del robot. A continuación, sigue los pasos de resolución de '
                          'problemas y retira cualquier posible obstrucción. (1028)',
               'title': 'El depósito de agua sucia o la cubeta de lavado de la mopa pueden estar '
                        'obstruidos'},
        'fr': {'content': 'Vérifiez s’il y a des fuites autour de la station d’accueil et videz le '
                          'bac d’eau sale du robot. Ensuite, suivez les étapes de dépannage pour '
                          'éliminer toute obstruction éventuelle. (1\xa0028)',
               'title': 'Le bac d’eau sale ou le bac de lavage de la lingette est peut-être '
                        'bouché'},
        'it': {'content': 'Cercare eventuali perdite intorno alla stazione di ricarica e svuotare '
                          "il serbatoio dell'acqua sporca del robot. Quindi, seguire i passaggi "
                          'per risolvere il problema e rimuovere eventuali ostruzioni. (1028)',
               'title': "Il serbatoio dell'acqua sporca o la vaschetta di lavaggio del panno "
                        'potrebbero essere ostruiti'},
        'nl': {'content': 'Controleer op lekken rond het basisstation en leeg de vuilwatertank van '
                          'de robot. Volg daarna de stappen om problemen op te lossen en eventuele '
                          'verstoppingen te verwijderen. (1028)',
               'title': 'Vuilwatertank of wasbak voor pads is mogelijk verstopt'},
        'pl': {'content': 'Sprawdź, czy wokół stacji dokującej brak wycieków i opróżnij zbiornik '
                          'na brudną wodę robota. Następnie postępuj zgodnie z instrukcjami, aby '
                          'rozwiązać problem i usunąć ewentualne zatory. (1028)',
               'title': 'Zbiornik na brudną wodę lub niecka myjąca nakładki mogą być zatkane'},
        'pt': {'content': 'Verifique se existem fugas à volta da base e esvazie o depósito de água '
                          'suja do robô. De seguida, siga os passos para resolver e remover '
                          'possíveis obstruções. (1028)',
               'title': 'O depósito de água suja ou o recipiente de lavagem pode estar obstruído'}},
 1029: {'de': {'content': 'Bitte löschen Sie die aktuelle Karte von @val und senden Sie den '
                          'Roboter los, um über die Registerkarte "Mein Zuhause" eine neue Karte '
                          'zu erstellen. (1029)',
               'title': 'Inkompatible Karte'},
        'en': {'content': "Please delete\xa0@val's current map and send it to create a new map "
                          'from the My Home tab. (1029)',
               'title': 'Map Incompatible'},
        'es': {'content': 'Elimina el mapa actual de @val y envíalo a crear uno nuevo desde la '
                          'pestaña Mi casa. (1029)',
               'title': 'Mapa incompatible'},
        'fr': {'content': 'Veuillez supprimer la carte actuelle de @val et ordonnez-lui de créer '
                          'une nouvelle carte à partir de l’onglet Mon domicile. (1\xa0029)',
               'title': 'Carte incompatible'},
        'it': {'content': 'Eliminare la mappa attuale di @val e creare una nuova mappa dalla '
                          'scheda La mia casa. (1029)',
               'title': 'Mappa incompatibile'},
        'nl': {'content': 'Verwijder de huidige kaart van @val en stuur hem/haar opnieuw in om een '
                          'nieuwe kaart te maken vanaf het tabblad my home. (1029)',
               'title': 'Incompatibele kaart'},
        'pl': {'content': 'Usuń obecną mapę robota @val i wyślij go, aby utworzył nową mapę w '
                          'zakładce Mój dom. (1029)',
               'title': 'Niekompatybilna mapa'},
        'pt': {'content': 'Elimine o mapa atual de @val e envie-o para criar um novo mapa a partir '
                          'do separador A minha casa. (1029)',
               'title': 'Mapa incompatível'}},
 1030: {'de': {'content': 'Bewegen Sie @val an einen neuen Ort und setzen Sie die Reinigung fort. '
                          '(1030)',
               'title': '@val hat sich in einer Nicht-Wischen-Zone festgefahren'},
        'en': {'content': 'Move\xa0@val\xa0to a new location and resume cleaning. (1030)',
               'title': '@val\xa0got stuck in a No Mop Zone'},
        'es': {'content': 'Mueve @val a una nueva ubicación y reanuda la limpieza. (1030)',
               'title': '@val se ha atascado en una zona de no fregado'},
        'fr': {'content': 'Déplacez @val vers un nouvel endroit et reprenez le nettoyage. (1\xa0'
                          '030)',
               'title': '@val est bloqué dans une zone sans nettoyage à la serpillière'},
        'it': {'content': 'Spostare @val in una nuova posizione e riprendere la pulizia. (1030)',
               'title': '@val si è bloccato in una Zona di lavaggio vietato'},
        'nl': {'content': 'Plaats @val op een nieuwe locatie en hervat de reiniging. (1030)',
               'title': "@val is vastgelopen in een 'Niet-dweilen-zone'"},
        'pl': {'content': 'Przenieś robota @val w nowe miejsce i wznów sprzątanie. (1030)',
               'title': 'Robot @val utknął w strefie bez mopa'},
        'pt': {'content': 'Mova @val para uma nova localização e retome a limpeza. (1030)',
               'title': '@val ficou preso numa Zona Sem Mopa'}},
 1034: {'de': {'content': 'Bringen Sie die Wischtuchplatte von @val wieder an und drücken Sie die '
                          'Ein-/Aus-Taste, um das Wischen fortzusetzen. (1034)',
               'title': 'Wischtuchplatte hat sich gelöst'},
        'en': {'content': 'Reinstall\xa0@val’s Pad Plate and press the Power button to resume '
                          'mopping. (1034)',
               'title': 'Pad Plate came off'},
        'es': {'content': 'Vuelve a instalar el soporte de la mopa de @val y pulsa el botón de '
                          'encendido para reanudar el fregado. (1034)',
               'title': 'Se ha soltado el soporte de la mopa'},
        'fr': {'content': 'Réinstallez le support de lingette de @val et appuyez sur le bouton '
                          'd’alimentation pour reprendre le nettoyage à la serpillière. (1\xa0034)',
               'title': 'Le support de lingette s’est enlevé'},
        'it': {'content': 'Reinstallare la piastra del panno di @val e premere il pulsante di '
                          'accensione per riprendere il lavaggio. (1034)',
               'title': 'Piastra del panno staccata'},
        'nl': {'content': 'Plaats de dweilplaat van @val terug en druk op de aan/uit-knop om het '
                          'dweilen te hervatten. (1034)',
               'title': 'Dweilplaat is losgeraakt'},
        'pl': {'content': 'Ponownie zamontuj płytkę nakładki robota @val i naciśnij przycisk '
                          'zasilania, aby wznowić mycie mopem. (1034)',
               'title': 'Odłączyła się płytka nakładki'},
        'pt': {'content': 'Volte a instalar a placa da mopa de @val e prima o botão de alimentação '
                          'para retomar a lavagem. (1034)',
               'title': 'A placa da mopa soltou-se'}},
 3212: {'de': {'content': 'Vergewissern Sie sich, dass Ihr Telefon mit dem Wi-Fi verbunden ist. '
                          'Wenn weiterhin Probleme auftreten, stellen Sie die Verbindung über das '
                          'Mobilfunknetz erneut her. (C210)',
               'title': 'Start nicht möglich: Verbinden Sie Ihr Telefon erneut mit dem Wi-Fi'},
        'en': {'content': 'Check that your phone is connected to Wi-Fi. If you’re still having '
                          'issues, reconnect using Cellular Data. (C210)',
               'title': 'Unable to start: Reconnect your phone to Wi-Fi'},
        'es': {'content': 'Comprueba que tu teléfono esté conectado al Wi-Fi. Si sigues teniendo '
                          'problemas, vuelve a conectarte usando los datos móviles. (C210)',
               'title': 'No se puede iniciar: Vuelve a conectar el teléfono al Wi-Fi'},
        'fr': {'content': 'Vérifiez que votre téléphone est connecté au Wi-Fi. Si vous rencontrez '
                          'toujours des problèmes, reconnectez-vous à l’aide des données '
                          'cellulaires. (C210)',
               'title': 'Impossible de démarrer : Reconnectez votre téléphone au Wi-Fi'},
        'it': {'content': 'Verificare che il telefono sia connesso al Wi-Fi. Se si continua a '
                          'riscontrare problemi, riconnettersi utilizzando i dati cellulare. '
                          '(C210)',
               'title': 'Impossibile avviare: Riconnettere il telefono alla rete Wi-Fi'},
        'nl': {'content': 'Controleer of je telefoon met Wi-Fi is verbonden. Als je nog steeds '
                          'problemen ondervindt, maak dan opnieuw verbinding via mobiele data. '
                          '(C210)',
               'title': 'Starten mislukt: Verbind je telefoon opnieuw met Wi-Fi'},
        'pl': {'content': 'Sprawdź, czy telefon jest podłączony do sieci Wi-Fi. Jeśli nadal '
                          'występują problemy, połącz ponownie przy użyciu danych komórkowych. '
                          '(C210)',
               'title': 'Nie można rozpocząć: Ponownie podłącz telefon do sieci Wi-Fi'},
        'pt': {'content': 'Verifique se o seu telemóvel está ligado ao Wi-Fi. Se o problema '
                          'persistir, reconecte utilizando os Dados Móveis. (C210)',
               'title': 'Não é possível iniciar: Reconecte o seu telemóvel ao Wi-Fi'}},
 3310: {'de': {'content': 'Tippen Sie auf "So wird\'s gemacht", um in wenigen schnellen Schritten '
                          'die App-Verbindung wiederherzustellen, damit @val weiter reinigen kann. '
                          '(C310)',
               'title': 'Roboter-Verbindungsfehler'},
        'en': {'content': 'Tap “Show me how” to follow quick steps to reconnect the app and get\xa0'
                          '@val\xa0back to cleaning. (C310)',
               'title': 'Robot connection abnormal'},
        'es': {'content': 'Toca “Mostrar cómo” para seguir unos rápidos pasos para volver a '
                          'conectar la app y que @val vuelva a limpiar. (C310)',
               'title': 'Conexión anómala del robot'},
        'fr': {'content': 'Appuyez sur “Montrez-moi comment” pour suivre les étapes rapides afin '
                          'de reconnecter l’application et de permettre à @val de reprendre le '
                          'nettoyage. (C310)',
               'title': 'Connexion anormale du robot'},
        'it': {'content': 'Toccare “Mostrami come” per seguire dei rapidi passaggi per '
                          "riconnettere l'app e far riprendere @val a pulire. (C310)",
               'title': 'Connessione anomala del robot'},
        'nl': {'content': 'Tik op ‘Laat me zien hoe’ om de snelle stappen te volgen om de app '
                          'opnieuw te verbinden en @val weer te laten schoonmaken. (C310)',
               'title': 'Afwijkende robotverbinding'},
        'pl': {'content': 'Stuknij przycisk „Pokaż mi jak”, aby wykonać szybkie kroki w celu '
                          'ponownego podłączenia aplikacji i przywrócenia robota @val do '
                          'sprzątania. (C310)',
               'title': 'Nieprawidłowe połączenie z robotem'},
        'pt': {'content': 'Toque em "Mostrar como" para seguir os passos rápidos e voltar a ligar '
                          'a aplicação e retomar a limpeza de @val. (C310)',
               'title': 'Ligação anómala do robô'}},
 4001: {'de': {'content': 'Lassen Sie @val auf seiner Dockingstation und vergewissern Sie sich, '
                          'dass eine gute Wi-Fi-Verbindung besteht.\n'
                          '\n'
                          'Bestimmte Funktionen sind erst nach Abschluss des Updates verfügbar. '
                          'Wir werden weiterhin im Hintergrund versuchen, das Update '
                          'durchzuführen. (4001)',
               'title': 'Bei der Aktualisierung von @val ist ein Problem aufgetreten'},
        'en': {'content': 'Keep\xa0@val\xa0on its dock and make sure you have a good Wi-Fi '
                          'connection.\n'
                          'Certain features will not be available until update is complete. We '
                          'will continue retrying the update in the background. (4001)',
               'title': '@val\xa0is having some trouble updating'},
        'es': {'content': 'Mantén a @val en su base y asegúrate de tener una buena conexión '
                          'Wi-Fi.\n'
                          '\n'
                          'Algunas funciones no estarán disponibles hasta que se complete la '
                          'actualización. Seguiremos intentando realizar la actualización en '
                          'segundo plano. (4001)',
               'title': '@val tiene problemas para actualizarse'},
        'fr': {'content': 'Laissez @val sur sa station d’accueil et assurez-vous de disposer d’une '
                          'bonne connexion Wi-Fi.\n'
                          '\n'
                          'Certaines fonctionnalités ne seront pas disponibles tant que la mise à '
                          'jour n’est pas terminée. Nous continuerons d’essayer d’effectuer la '
                          'mise à jour en arrière-plan. (4001)',
               'title': '@val rencontre des problèmes de mise à jour'},
        'it': {'content': 'Tenere @val sulla base e assicurarsi di avere una buona connessione '
                          'Wi-Fi.\n'
                          '\n'
                          "Alcune funzioni non saranno disponibili finché l'aggiornamento non sarà "
                          "completato. Continueremo a ritentare l'aggiornamento in background. "
                          '(4001)',
               'title': "@val sta riscontrando problemi durante l'aggiornamento"},
        'nl': {'content': 'Houd @val op het dock en zorg voor een goede Wi-Fi-verbinding.\n'
                          '\n'
                          'Bepaalde functies zijn niet beschikbaar totdat de update is voltooid. '
                          'We blijven de update op de achtergrond opnieuw proberen. (4001)',
               'title': '@val heeft wat problemen met het updaten'},
        'pl': {'content': 'Pozostaw robota @val w stacji dokującej i upewnij się, że masz dobre '
                          'połączenie z siecią Wi-Fi.\n'
                          '\n'
                          'Niektóre funkcje nie będą dostępne do momentu zakończenia aktualizacji. '
                          'Będziemy kontynuować próby aktualizacji w tle. (4001)',
               'title': 'Wystąpił problem z aktualizacją robota @val'},
        'pt': {'content': 'Mantenha @val na base e certifique-se de que tem uma boa ligação '
                          'Wi-Fi.\n'
                          '\n'
                          'Algumas funcionalidades não estarão disponíveis até que a atualização '
                          'esteja concluída. Continuaremos a tentar atualizar em segundo plano. '
                          '(4001)',
               'title': '@val está com alguns problemas de atualização'}},
 4002: {'de': {'content': 'Vergewissern Sie sich, dass @val bei guter Wi-Fi-Verbindung angedockt '
                          'ist. Wir versuchen weiterhin, das Update im Hintergrund durchzuführen, '
                          'und senden eine Benachrichtigung, wenn es fertig ist. (4002)',
               'title': 'Bei der Aktualisierung von @val ist ein Problem aufgetreten'},
        'en': {'content': "Make sure\xa0@val\xa0is docked with a good Wi-Fi connection. We'll keep "
                          "trying the update in the background and send a notification when it's "
                          'complete. (4002)',
               'title': '@val\xa0is having some trouble updating'},
        'es': {'content': 'Asegúrate de que @val esté en la base y tenga una buena conexión Wi-Fi. '
                          'Seguiremos intentando realizar la actualización en segundo plano y '
                          'enviaremos una notificación cuando se haya completado. (4002)',
               'title': '@val tiene problemas para actualizarse'},
        'fr': {'content': 'Assurez-vous que @val est sur sa station d’accueil et dispose d’une '
                          'bonne connexion Wi-Fi. Nous continuerons d’essayer d’effectuer la mise '
                          'à jour en arrière-plan et vous enverrons une notification lorsqu’elle '
                          'sera terminée. (4002)',
               'title': '@val rencontre des problèmes de mise à jour'},
        'it': {'content': 'Assicurarsi che @val sia posizionato sulla base con una buona '
                          "connessione Wi-Fi. Continueremo a provare a eseguire l'aggiornamento in "
                          'background e invieremo una notifica una volta completato. (4002)',
               'title': "@val sta riscontrando problemi durante l'aggiornamento"},
        'nl': {'content': 'Zorg ervoor dat @val op het basisstation staat en een goede '
                          'Wi-Fi-verbinding heeft. We blijven de update op de achtergrond proberen '
                          'en sturen een melding wanneer deze voltooid is. (4002)',
               'title': '@val heeft wat problemen met het updaten'},
        'pl': {'content': 'Upewnij się, że robot @val znajduje się w stacji dokującej i ma dobre '
                          'połączenie z siecią Wi-Fi. Będziemy nadal próbować przeprowadzić '
                          'aktualizację w tle i wyślemy powiadomienie, gdy się zakończy. (4002)',
               'title': 'Wystąpił problem z aktualizacją robota @val'},
        'pt': {'content': 'Certifique-se de que @val está na base e ligado a uma rede Wi-Fi com '
                          'bom sinal. Continuaremos a tentar atualizar em segundo plano e '
                          'enviar-lhe-emos uma notificação quando estiver concluída. (4002)',
               'title': '@val está com alguns problemas de atualização'}},
 4003: {'de': {'content': 'Dies kann bis zu 1 Stunde dauern. Belassen Sie @val auf seiner '
                          'Dockingstation, bis das Update fertig ist. (4003)',
               'title': 'Roboter wird aktualisiert'},
        'en': {'content': 'This can take up to 1h. Keep\xa0@val\xa0on its Dock until update is '
                          'complete. (4003)',
               'title': 'Robot is updating'},
        'es': {'content': 'Este proceso puede tardar hasta 1\xa0hora. Deja @val en su base hasta '
                          'que se complete la actualización. (4003)',
               'title': 'El robot se está actualizando'},
        'fr': {'content': 'Cela peut prendre jusqu’à 1 heure. Laissez @val sur sa station '
                          'd’accueil jusqu’à ce que la mise à jour soit terminée. (4\xa0003)',
               'title': 'Le robot est en cours de mise à jour'},
        'it': {'content': 'Potrebbe richiedere fino a 1 ora. Lasciare @val sulla base fino al '
                          "completamento dell'aggiornamento. (4003)",
               'title': 'Il robot è in aggiornamento'},
        'nl': {'content': 'Dit kan tot 1 uur duren. Laat @val op het basisstation staan tot de '
                          'update is voltooid. (4003)',
               'title': 'De robot wordt bijgewerkt'},
        'pl': {'content': 'Może to potrwać maksymalnie godzinę. Pozostaw robota @val w stacji '
                          'dokującej do zakończenia aktualizacji. (4003)',
               'title': 'Robot jest aktualizowany'},
        'pt': {'content': 'Isto pode demorar até 1 hora. Mantenha @val na base até a atualização '
                          'estar concluída. (4003)',
               'title': 'O robô está a ser atualizado'}},
 4004: {'de': {'content': 'Dies kann bis zu 1 Stunde dauern. Belassen Sie @val auf seiner '
                          'Dockingstation, bis das Update fertig ist. (4004)',
               'title': 'Roboter wird aktualisiert'},
        'en': {'content': 'This can take up to 1h. Keep\xa0@val\xa0on its Dock until update is '
                          'complete. (4004)',
               'title': 'Robot is updating'},
        'es': {'content': 'Este proceso puede tardar hasta 1\xa0hora. Deja @val en su base hasta '
                          'que se complete la actualización. (4004)',
               'title': 'El robot se está actualizando'},
        'fr': {'content': 'Cela peut prendre jusqu’à 1 heure. Laissez @val sur sa station '
                          'd’accueil jusqu’à ce que la mise à jour soit terminée. (4\xa0004)',
               'title': 'Le robot est en cours de mise à jour'},
        'it': {'content': 'Potrebbe richiedere fino a 1 ora. Lasciare @val sulla base fino al '
                          "completamento dell'aggiornamento. (4004)",
               'title': 'Il robot è in aggiornamento'},
        'nl': {'content': 'Dit kan tot 1 uur duren. Laat @val op het basisstation staan tot de '
                          'update is voltooid. (4004)',
               'title': 'De robot wordt bijgewerkt'},
        'pl': {'content': 'Może to potrwać maksymalnie godzinę. Pozostaw robota @val w stacji '
                          'dokującej do zakończenia aktualizacji. (4004)',
               'title': 'Robot jest aktualizowany'},
        'pt': {'content': 'Isto pode demorar até 1 hora. Mantenha @val na base até a atualização '
                          'estar concluída. (4004)',
               'title': 'O robô está a ser atualizado'}}}


#: iRobot's own strings use more than one placeholder form, and two of
#: them are broken.
#:
#:   `@val`            667 times -- the normal one
#:   `%robotName`      once, in English code 251
#:   `@valAsegúrate`   the placeholder run together with the next word,
#:   `@valUpewnij`     in Spanish and Polish -- a translation artefact
#:   `@valPozostaw`    where a space was lost
#:
#: Left as data and repaired on the way out, so the catalogue stays a
#: faithful copy of what iRobot ships.
#: A capital letter straight after `@val` is the giveaway: no language
#: here continues a word that way, so it is always the lost space.
_GLUED = re.compile(r"@val(?=[A-ZÁÉÍÓÚÑÜÖÄŻŁŚĆ])")


def _tidy(text: str) -> str:
    """Normalises the placeholder without translating the sentence.

    A user seeing `%robotName` or `@valUpewnij` reads a bug, and it is
    iRobot's -- but it is ours to display. Splitting the glued ones puts
    the missing space back; unifying `%robotName` means a caller has one
    token to substitute rather than two.
    """
    return _GLUED.sub("@val ", text).replace("%robotName", "@val")


def vendor_error(code: Any, language: str = "en") -> dict[str, str] | None:
    """iRobot's own title and explanation for a code, or None.

    Falls back to English when the requested language is not one of the
    eight extracted -- an English sentence that says what to do beats a
    localised label that does not.
    """
    try:
        entry = VENDOR_ERROR_TEXTS.get(int(code))
    except (TypeError, ValueError):
        return None
    if entry is None:
        return None
    found = entry.get(str(language).split("-")[0].lower()) or entry.get("en")
    if found is None:
        return None
    return {k: _tidy(v) if isinstance(v, str) else v for k, v in found.items()}
