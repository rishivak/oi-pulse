FROM node:20-alpine

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["node", "./node_modules/next/dist/bin/next", "dev", "--hostname", "0.0.0.0"]