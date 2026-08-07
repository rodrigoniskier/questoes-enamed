import os
from PIL import Image

# Caminho relativo para a pasta onde o Django guarda as imagens das questões
diretorio = 'media/imagens_questoes'

def comprimir_imagens():
    if not os.path.exists(diretorio):
        print(f"Erro: O diretório '{diretorio}' não foi encontrado.")
        print("Certifique-se de estar rodando este script na pasta principal do projeto.")
        return

    arquivos = os.listdir(diretorio)
    total_imagens = 0

    print(f"Iniciando varredura em {len(arquivos)} ficheiros...\n")

    for nome_arquivo in arquivos:
        caminho_completo = os.path.join(diretorio, nome_arquivo)

        # Ignorar se for uma pasta
        if not os.path.isfile(caminho_completo):
            continue

        try:
            # Abre a imagem
            img = Image.open(caminho_completo)
            modificado = False
            tamanho_original = os.path.getsize(caminho_completo)

            # 1. Redimensionar se for muito larga (> 800px)
            max_width = 800
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_height = int(float(img.height) * float(ratio))
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                modificado = True

            # 2. Guardar por cima, respeitando o formato original para não quebrar o Banco de Dados
            if img.format == 'JPEG' or nome_arquivo.lower().endswith(('.jpg', '.jpeg')):
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(caminho_completo, format='JPEG', quality=70, optimize=True)

            elif img.format == 'PNG' or nome_arquivo.lower().endswith('.png'):
                # Otimiza o PNG sem perder a transparência
                img.save(caminho_completo, format='PNG', optimize=True)

            else:
                # Outros formatos (gif, bmp): salva redimensionado se necessário
                if modificado:
                    img.save(caminho_completo)

            tamanho_novo = os.path.getsize(caminho_completo)
            economia = (tamanho_original - tamanho_novo) / 1024 # em KB

            # Mostra o resultado apenas se a imagem tiver sido esmagada
            if tamanho_novo < tamanho_original:
                print(f"✅ {nome_arquivo}: Reduzido em {economia:.1f} KB")
                total_imagens += 1

        except Exception as e:
            print(f"⚠️ Ignorado ({nome_arquivo}): Não é uma imagem válida ou erro ({e})")

    print(f"\n🚀 Limpeza concluída! {total_imagens} imagens foram otimizadas com sucesso.")

if __name__ == "__main__":
    comprimir_imagens()