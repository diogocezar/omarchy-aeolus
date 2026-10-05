pragma Singleton
import QtQuick

// UI strings. `language` is "auto" (follow the system locale) or one of the
// keys below; anything missing falls back to English. Add a language by
// adding a table — every key in `en` is used somewhere.
QtObject {
    id: root

    property string language: "auto"

    readonly property string resolved: {
        var lang = language === "auto" ? Qt.locale().name.split("_")[0] : language;
        return tables[lang] ? lang : "en";
    }

    readonly property var tables: ({
        en: {
            outdated: "The installed service (%1) is older than the plugin. Update it with:",
            tooltip: "Aeolus · %1",
            offline: "Fan control is off",
            offlineHint: "The Aeolus service isn't running, so the fans follow the motherboard's own curves. Install it once with:",
            offlineStopped: "The service is installed but stopped. Start it with:",
            copy: "Copy command", copied: "Copied",
            temperatures: "Temperature", cpu: "CPU", gpu: "GPU",
            target: "target %1", limit: "100% at %1",
            mode: "Mode",
            mode_auto: "Auto", mode_silent: "Silent", mode_performance: "Performance", mode_manual: "Manual",
            hint_auto: "As quiet as it gets while the CPU stays under %1: the fans only speed up when a load pushes past it.",
            hint_silent: "A fixed low curve: the CPU may run warmer before the fans speed up.",
            hint_performance: "A fixed high curve: cooler and louder.",
            hint_manual: "The fans hold the speed you set, but never less than Silent would ask for at this temperature.",
            speed: "Fan speed", speedHint: "Drag or scroll to set the speed (switches to Manual)",
            headers: "Fans", pump: "Pump", fans: "Fans", stopped: "stopped",
            reason_emergency: "Too hot: everything at full speed",
            reason_stalled: "A fan stopped turning: everything at full speed",
            "reason_no-sensor": "No temperature reading: everything at full speed",
            rails: "Always on: 100% at %1 · pump never under its floor · back to the motherboard's curves if the service stops.",
            error: "Couldn't change the mode"
        },
        pt: {
            outdated: "O serviço instalado (%1) é mais antigo que o plugin. Atualize com:",
            tooltip: "Aeolus · %1",
            offline: "Controle dos fans desligado",
            offlineHint: "O serviço do Aeolus não está rodando, então os fans seguem as curvas da placa-mãe. Instale uma vez com:",
            offlineStopped: "O serviço está instalado mas parado. Ligue com:",
            copy: "Copiar comando", copied: "Copiado",
            temperatures: "Temperatura", cpu: "CPU", gpu: "GPU",
            target: "alvo %1", limit: "100% em %1",
            mode: "Modo",
            mode_auto: "Auto", mode_silent: "Silencioso", mode_performance: "Desempenho", mode_manual: "Manual",
            hint_auto: "O mais silencioso possível com a CPU abaixo de %1: os fans só aceleram quando uma carga passa disso.",
            hint_silent: "Uma curva baixa fixa: a CPU pode esquentar mais antes de os fans acelerarem.",
            hint_performance: "Uma curva alta fixa: mais fresco e mais barulhento.",
            hint_manual: "Os fans ficam na velocidade que você escolher, mas nunca abaixo do que o Silencioso pediria nessa temperatura.",
            speed: "Velocidade dos fans", speedHint: "Arraste ou role para mudar a velocidade (passa para Manual)",
            headers: "Fans", pump: "Bomba", fans: "Fans", stopped: "parado",
            reason_emergency: "Quente demais: tudo no máximo",
            reason_stalled: "Um fan parou de girar: tudo no máximo",
            "reason_no-sensor": "Sem leitura de temperatura: tudo no máximo",
            rails: "Sempre valendo: 100% em %1 · bomba nunca abaixo do piso · volta para as curvas da placa-mãe se o serviço parar.",
            error: "Não foi possível trocar o modo"
        },
        es: {
            outdated: "El servicio instalado (%1) es más antiguo que el plugin. Actualízalo con:",
            tooltip: "Aeolus · %1",
            offline: "Control de ventiladores apagado",
            offlineHint: "El servicio de Aeolus no está en marcha, así que los ventiladores siguen las curvas de la placa base. Instálalo una vez con:",
            offlineStopped: "El servicio está instalado pero detenido. Inícialo con:",
            copy: "Copiar comando", copied: "Copiado",
            temperatures: "Temperatura", cpu: "CPU", gpu: "GPU",
            target: "objetivo %1", limit: "100% a %1",
            mode: "Modo",
            mode_auto: "Auto", mode_silent: "Silencioso", mode_performance: "Rendimiento", mode_manual: "Manual",
            hint_auto: "Lo más silencioso posible con la CPU por debajo de %1: los ventiladores solo aceleran cuando una carga lo supera.",
            hint_silent: "Una curva baja fija: la CPU puede calentarse más antes de que aceleren.",
            hint_performance: "Una curva alta fija: más fresco y más ruidoso.",
            hint_manual: "Los ventiladores mantienen la velocidad elegida, pero nunca menos de lo que pediría Silencioso a esta temperatura.",
            speed: "Velocidad", speedHint: "Arrastra o desplaza para cambiar la velocidad (pasa a Manual)",
            headers: "Ventiladores", pump: "Bomba", fans: "Ventiladores", stopped: "parado",
            reason_emergency: "Demasiado caliente: todo al máximo",
            reason_stalled: "Un ventilador dejó de girar: todo al máximo",
            "reason_no-sensor": "Sin lectura de temperatura: todo al máximo",
            rails: "Siempre activo: 100% a %1 · la bomba nunca baja de su mínimo · vuelve a las curvas de la placa si el servicio se detiene.",
            error: "No se pudo cambiar el modo"
        },
        fr: {
            outdated: "Le service installé (%1) est plus ancien que le plugin. Mettez-le à jour avec :",
            tooltip: "Aeolus · %1",
            offline: "Contrôle des ventilateurs désactivé",
            offlineHint: "Le service Aeolus ne tourne pas : les ventilateurs suivent les courbes de la carte mère. Installez-le une fois avec :",
            offlineStopped: "Le service est installé mais arrêté. Démarrez-le avec :",
            copy: "Copier la commande", copied: "Copié",
            temperatures: "Température", cpu: "CPU", gpu: "GPU",
            target: "cible %1", limit: "100 % à %1",
            mode: "Mode",
            mode_auto: "Auto", mode_silent: "Silencieux", mode_performance: "Performance", mode_manual: "Manuel",
            hint_auto: "Le plus silencieux possible tant que le CPU reste sous %1 : les ventilateurs n'accélèrent que si une charge le dépasse.",
            hint_silent: "Une courbe basse fixe : le CPU peut chauffer davantage avant que les ventilateurs accélèrent.",
            hint_performance: "Une courbe haute fixe : plus frais et plus bruyant.",
            hint_manual: "Les ventilateurs gardent la vitesse choisie, jamais moins que ce que Silencieux demanderait à cette température.",
            speed: "Vitesse", speedHint: "Glissez ou faites défiler pour régler la vitesse (passe en Manuel)",
            headers: "Ventilateurs", pump: "Pompe", fans: "Ventilateurs", stopped: "arrêté",
            reason_emergency: "Trop chaud : tout à fond",
            reason_stalled: "Un ventilateur s'est arrêté : tout à fond",
            "reason_no-sensor": "Pas de température lue : tout à fond",
            rails: "Toujours actif : 100 % à %1 · la pompe ne descend jamais sous son plancher · retour aux courbes de la carte si le service s'arrête.",
            error: "Impossible de changer le mode"
        },
        de: {
            outdated: "Der installierte Dienst (%1) ist älter als das Plugin. Aktualisieren mit:",
            tooltip: "Aeolus · %1",
            offline: "Lüftersteuerung aus",
            offlineHint: "Der Aeolus-Dienst läuft nicht, die Lüfter folgen den Kurven des Mainboards. Einmal installieren mit:",
            offlineStopped: "Der Dienst ist installiert, aber gestoppt. Starten mit:",
            copy: "Befehl kopieren", copied: "Kopiert",
            temperatures: "Temperatur", cpu: "CPU", gpu: "GPU",
            target: "Ziel %1", limit: "100 % bei %1",
            mode: "Modus",
            mode_auto: "Auto", mode_silent: "Leise", mode_performance: "Leistung", mode_manual: "Manuell",
            hint_auto: "So leise wie möglich, solange die CPU unter %1 bleibt: die Lüfter drehen nur hoch, wenn eine Last darüber treibt.",
            hint_silent: "Eine feste niedrige Kurve: die CPU darf wärmer werden, bevor die Lüfter hochdrehen.",
            hint_performance: "Eine feste hohe Kurve: kühler und lauter.",
            hint_manual: "Die Lüfter halten die gewählte Drehzahl, aber nie weniger, als Leise bei dieser Temperatur verlangen würde.",
            speed: "Lüftergeschwindigkeit", speedHint: "Ziehen oder scrollen, um die Geschwindigkeit zu ändern (wechselt zu Manuell)",
            headers: "Lüfter", pump: "Pumpe", fans: "Lüfter", stopped: "steht",
            reason_emergency: "Zu heiß: alles auf Maximum",
            reason_stalled: "Ein Lüfter dreht nicht mehr: alles auf Maximum",
            "reason_no-sensor": "Keine Temperatur: alles auf Maximum",
            rails: "Immer aktiv: 100 % bei %1 · Pumpe nie unter ihrem Minimum · zurück zu den Mainboard-Kurven, wenn der Dienst stoppt.",
            error: "Modus konnte nicht geändert werden"
        }
    })

    function t(key, arg) {
        var table = tables[resolved] || tables.en;
        var s = table[key] !== undefined ? table[key] : (tables.en[key] !== undefined ? tables.en[key] : key);
        return arg !== undefined ? s.replace("%1", arg) : s;
    }
}
