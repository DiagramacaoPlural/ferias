# Controle de Férias

Sistema web para programar, aprovar e acompanhar as férias da equipe. Substitui a
planilha `Férias_Final.xlsm` (formulário VBA + abas CALENDARIO, Cronograma e USUARIOS).

## Como iniciar

**Jeito mais fácil (Windows):** dê dois cliques em `iniciar.bat`.
Na primeira vez ele cria o ambiente e instala as dependências; depois abre o navegador em
<http://127.0.0.1:5000>.

**Manualmente:**

```bash
pip install -r requirements.txt
python run.py            # só neste computador
python run.py --rede     # libera para outros computadores da rede (http://SEU-IP:5000)
```

Na primeira execução o sistema cria o banco `instance/ferias.db` e **importa automaticamente
a planilha `.xlsm` que estiver na pasta** (funcionários, férias e usuários).

### Primeiro acesso

Use os mesmos usuários e senhas da aba USUARIOS da planilha (ex.: `Gercilio` / `123`).
No primeiro login o sistema exige a troca da senha. Se não houver planilha, é criado o usuário
`admin` com senha `admin`.

## Funcionalidades

| Tela | O que faz |
|---|---|
| **Painel** | Indicadores do ciclo, quem está de férias hoje, próximos 60 dias, pendentes de aprovação, conflitos e saldo de dias por funcionário. |
| **Programação** | Lista, cadastro, edição, exclusão e aprovação de férias, com filtros e exportação para Excel. |
| **Calendário anual** | Os 12 meses do ciclo com as férias programadas e aprovadas (equivale aos painéis do formulário VBA). |
| **Cronograma** | Gráfico de Gantt com navegação por semanas, destaque de fins de semana e do dia de hoje, e linha de ausentes por dia. |
| **Funcionários / Usuários** | Cadastros com permissões (administrador, acesso à programação, acesso ao cronograma). |
| **Configurações** | Mês de início do ciclo, limite de pessoas de férias ao mesmo tempo, janela do cronograma e importação de planilha. |
| **Histórico** | Registro de quem fez o quê (criação, alteração, aprovação, exclusão, login). |

## Melhorias em relação à planilha

- **Cálculo do fim corrigido:** o dia de início conta como 1º dia (20 dias a partir de 01/12 terminam em 20/12; a planilha calculava 21/12).
- **Ciclo configurável:** o período maio/2026–abril/2027 não está mais fixo no código; é possível navegar entre ciclos.
- **Vários períodos por funcionário** (fracionamento das férias).
- **Validações da CLT (art. 134)** como avisos: mínimo de 5 dias por período, até 3 períodos, um período com pelo menos 14 dias, início vedado na sexta ou sábado, total acima de 30 dias no ciclo.
- **Bloqueio de sobreposição** de períodos do mesmo funcionário.
- **Alerta de conflito na equipe** quando o número de pessoas de férias ao mesmo tempo passa do limite configurado (a planilha só sinalizava no Cronograma).
- **Validação ao vivo** no formulário: o fim e os avisos aparecem enquanto se digita.
- **Segurança:** login real, senhas criptografadas, troca obrigatória da senha inicial, proteção CSRF e permissões aplicadas no servidor.
- **Aprovação** registra quem aprovou e quando; somente administradores aprovam ou alteram férias já aprovadas.
- **Multiusuário:** várias pessoas podem usar ao mesmo tempo pelo navegador, sem conflito de arquivo.
- **Exportação para Excel** com aba de resumo por funcionário.

Observação: a validação de início antes do repouso considera domingo como dia de descanso; feriados não são verificados.

## Estrutura

```
app/
  models.py      tabelas (usuários, funcionários, férias, configurações, histórico)
  regras.py      regras de negócio (cálculo do fim, ciclo, validações CLT, conflitos)
  consultas.py   consultas reutilizadas pelas telas
  seed.py        importação da planilha e carga inicial
  views/         rotas (auth, painel, férias, cronograma, admin)
  templates/     páginas HTML
  static/        CSS
tests/           testes automatizados (python -m pytest)
run.py           inicia o servidor
iniciar.bat      atalho para Windows
```

## Backup

Todos os dados ficam no arquivo `instance/ferias.db`. Para fazer backup, basta copiar esse arquivo
com o sistema parado.
