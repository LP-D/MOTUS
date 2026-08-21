# Notes de validation du corpus

## RECOUINAS (groupe R,9 — meilleur coup 1 en cache)

**Question initiale** : `RECOUINAS` sort comme meilleur premier coup pour le groupe
(lettre imposée `R`, longueur `9`) dans `data/root_cache.json`. Ce n'est pas une
forme usuelle du français — vérification demandée avant de faire confiance au cache.

**Vérifications effectuées** :

1. **Présence dans le corpus source brut.** Le CSV brut du dépôt GitHub
   [Kartmaan/french-language-tools](https://github.com/Kartmaan/french-language-tools)
   (`files/dico.csv`, dérivé du Wiktionnaire francophone — cf. comparaison de
   corpus, session précédente) contient l'entrée :

   ```
   Mot: Recouinas
   Définitions: ["Deuxième personne du singulier du passé simple du verbe recouiner."]
   ```

   `RECOUINAS` = **"tu recouinas"**, passé simple 2e personne du singulier du verbe
   *recouiner* ("émettre un couinement répété", ex. une porte, une souris). C'est un
   temps littéraire rarement utilisé à l'oral, mais grammaticalement valide.

2. **Absence d'artefact de parsing.** Le mot apparaît dans une famille de
   conjugaison cohérente et complète, en ordre alphabétique dans
   `data/corpus_fr.txt` (lignes 178150-178160) :

   ```
   RECOUDRE
   RECOUDREZ
   RECOUDS
   RECOUINA
   RECOUINAI
   RECOUINAS   <-- l'entrée en question
   RECOUINAT
   RECOUINE
   RECOUINER
   RECOUINES
   RECOUINEZ
   ```

   Aucun signe de troncature, de concaténation de deux mots, ou de doublon d'une
   entrée voisine. Aucun caractère non-ASCII/mal encodé.

3. **Cohérence entre sources.** `RECOUINAS` figure à la fois dans le corpus actuel
   (`data/corpus_fr.txt`) et dans le CSV brut GitHub normalisé — ce n'est donc pas
   un artefact propre à une seule des deux sources.

**Conclusion : entrée légitime, rare mais correcte.** Aucune modification du corpus
ni régénération du cache n'est nécessaire.

**Ce que ça révèle en revanche** : le scoring composite (entropie + voyelles +
lettres distinctes + fréquence) n'a aucune notion de "forme usuelle" vs "forme
grammaticale rare" — un mot comme `RECOUINAS` (5 voyelles, 8 lettres distinctes sur
9) peut dominer le score composite précisément parce qu'il est riche en lettres
variées, sans que ce soit un bon choix *pratique* pour un joueur humain. C'est une
limite connue de l'heuristique de scoring actuelle, pas un bug de données — noté ici
à titre informatif, hors scope de cette vérification (le scoring composite n'a pas
été modifié, conformément à la contrainte du projet).
